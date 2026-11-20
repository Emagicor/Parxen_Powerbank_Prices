"""Flipkart pincode delivery checker.

For each pincode it opens a fresh browser context, sets the delivery location
once through the product page's "Select delivery location" drawer (the same
steps a person would take), then visits every product and reads the
"Delivery details" block. It goes slowly on purpose: one browser, one page,
a random pause between page loads.
"""
import os
import random
import re
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

import db

SCREENS = Path(__file__).parent / "data" / "screens"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
HEADLESS = os.environ.get("PARXEN_HEADED") != "1"


class PincodeNotFound(Exception):
    pass


class Blocked(Exception):
    pass


# ---------------------------------------------------------------- parsing

OOS_RE = re.compile(r"sold out|out of stock|currently unavailable|coming soon", re.I)
NOT_DELIV_RE = re.compile(r"not deliverable[^\n]*", re.I)
BLOCK_RE = re.compile(r"access denied|are you a human|unusual traffic|captcha", re.I)


def parse_product_page(body):
    """Turn the visible page text into a result dict."""
    if BLOCK_RE.search(body[:3000]):
        raise Blocked("Flipkart showed a block / captcha page")

    cut = body.find("Product highlights")
    top = body[:cut] if cut > 0 else body[:6000]
    flat = " ".join(top.split())

    i = top.find("Delivery details")
    dd = ""
    if i >= 0:
        j = top.find("Shop with peace of mind", i)
        dd = top[i:j if j > 0 else i + 600]
    lines = [l.strip() for l in dd.splitlines() if l.strip()]

    res = {"status": "UNKNOWN", "delivery_text": None, "cod": None,
           "seller": None, "price": None, "location_text": None}

    if len(lines) > 1:
        res["location_text"] = lines[1][:200]
    if "Location not set" in dd:
        res["status"] = "ERROR"
        res["delivery_text"] = "Location was not applied"
        return res

    m = NOT_DELIV_RE.search(top)
    if m:
        res["status"] = "NOT_DELIVERABLE"
        res["delivery_text"] = m.group(0).strip()
    elif "Delivery by" in lines:
        k = lines.index("Delivery by")
        res["status"] = "DELIVERABLE"
        res["delivery_text"] = lines[k + 1] if k + 1 < len(lines) else "Delivery by ?"
    elif OOS_RE.search(top):
        res["status"] = "OUT_OF_STOCK"
        res["delivery_text"] = OOS_RE.search(top).group(0)
    else:
        res["delivery_text"] = " | ".join(lines[:6])[:300] or None

    low = flat.lower()
    if "no cash on delivery" in low:
        res["cod"] = 0
    elif "cash on delivery" in low:
        res["cod"] = 1
    sm = re.search(r"Seller:\s*([^\n|]+)", top)
    if sm:
        res["seller"] = sm.group(1).strip()[:80]
    pm = re.search(r"₹\s?([\d,]+)", top)
    if pm:
        res["price"] = pm.group(1)
    return res


# ---------------------------------------------------------------- browser steps

def open_product(page, url):
    resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
    if resp is not None and resp.status in (403, 429):
        raise Blocked(f"HTTP {resp.status} from Flipkart")
    try:
        page.get_by_text("Delivery details").first.wait_for(timeout=20000)
    except PWTimeout:
        pass  # unavailable products may not show the block; parse anyway
    page.wait_for_timeout(1500)


def _open_location_drawer(page):
    """The drawer link ignores clicks until the page has hydrated, so retry."""
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except PWTimeout:
        pass
    box = page.get_by_placeholder("Search by area, street name, pin code")
    for _ in range(3):
        page.get_by_text("Select delivery location").first.click(timeout=15000)
        try:
            box.wait_for(timeout=8000)
            return box
        except PWTimeout:
            page.keyboard.press("Escape")
            page.wait_for_timeout(2000)
    raise RuntimeError("Location drawer did not open")


def _search_suggestion(page, box, query, pattern, tries):
    """Type `query` into the drawer and wait for a suggestion matching `pattern`."""
    sug = page.get_by_text(pattern).first
    for _ in range(tries):  # the list is sometimes empty on the first try: clear and retype
        box.click()
        box.fill("")
        page.wait_for_timeout(500)
        box.press_sequentially(query, delay=110)
        try:
            sug.wait_for(timeout=8000)
            return sug
        except PWTimeout:
            continue
    return None


def set_location(page, pin, city=None):
    """Open the location drawer on the current product page and pick `pin`.

    1. Search the bare pincode and take Flipkart's "<state> <pin> India" entry.
    2. If there is none (Flipkart's map search is patchy for some towns), search
       "<city> <pin>" and take a suggestion that carries the pincode.
    Then make sure the address Flipkart settled on really has that pincode.
    """
    box = _open_location_drawer(page)
    sug = _search_suggestion(page, box, pin, re.compile(rf"\b{pin}\s+India\b"), 3)
    if sug is None and city:
        sug = _search_suggestion(page, box, f"{city} {pin}", re.compile(rf"\b{pin}\b"), 2)
    if sug is None:
        raise PincodeNotFound(f"Flipkart did not suggest pincode {pin}")
    sug.click()
    deliver_to = None
    try:
        panel = page.get_by_text("Deliver To", exact=True).first
        panel.wait_for(timeout=12000)
        deliver_to = " ".join(panel.locator("xpath=..").inner_text().split())[:200]
    except PWTimeout:
        pass
    if deliver_to:
        found = re.findall(r"\b[1-9]\d{5}\b", deliver_to)
        if found and pin not in found:
            raise PincodeNotFound(f"Flipkart placed {pin} at pincode {found[-1]} instead")
    page.get_by_text("Confirm", exact=True).first.click(timeout=15000)
    # back on the product page: wait until the delivery block reflects the new location
    deadline = time.time() + 20
    while time.time() < deadline:
        page.wait_for_timeout(1000)
        try:
            body = page.inner_text("body")
        except Exception:
            continue
        if "Delivery details" in body and "Location not set" not in body:
            break
    page.wait_for_timeout(1500)
    return deliver_to


# ---------------------------------------------------------------- run

def _save_result(con, run_id, product, pin, res, error=None):
    # Only the latest result per (pincode, product) is kept, whichever run made it.
    con.execute("DELETE FROM results WHERE pincode=? AND product_id=?", (pin, product["id"]))
    con.execute(
        "INSERT INTO results(run_id, product_id, product_name, pincode, status, delivery_text, cod, "
        "seller, price, location_text, error, checked_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, product["id"], product["name"], pin, res.get("status", "ERROR"),
         res.get("delivery_text"), res.get("cod"), res.get("seller"), res.get("price"),
         res.get("location_text"), error, db.now()))
    con.execute("UPDATE runs SET done = done + 1 WHERE id=?", (run_id,))


def _stop_requested(run_id):
    with db.connect() as con:
        return con.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()["status"] == "stopping"


from snapshot import PIN_ORDER  # noqa: E402  (same pincode order everywhere)


def full_plan():
    """Every active pincode × every active product."""
    with db.connect() as con:
        products = [dict(r) for r in con.execute("SELECT * FROM products WHERE active=1 ORDER BY id")]
        pins = [r["pincode"] for r in con.execute(f"SELECT pincode FROM pincodes WHERE active=1 {PIN_ORDER}")]
    return [(pin, products) for pin in pins]


def plan_for(pairs):
    """Group [(pincode, product_id), …] by pincode, in the usual pincode order.
    Products deleted in the meantime are skipped."""
    with db.connect() as con:
        prods = {r["id"]: dict(r) for r in con.execute("SELECT * FROM products")}
        order = {r["pincode"]: i for i, r in enumerate(con.execute(f"SELECT pincode FROM pincodes {PIN_ORDER}"))}
    grouped = {}
    for pin, pid in dict.fromkeys(pairs):
        if pid in prods:
            grouped.setdefault(pin, []).append(prods[pid])
    return sorted(grouped.items(), key=lambda kv: order.get(kv[0], 10 ** 6))


def create_run(trigger, plan=None, message=None):
    """Log a new job. `plan` defaults to the full grid."""
    plan = plan if plan is not None else full_plan()
    total = sum(len(products) for _, products in plan)
    with db.connect() as con:
        cur = con.execute("INSERT INTO runs(started_at, status, trigger, total, message) VALUES (?,?,?,?,?)",
                          (db.now(), "running", trigger, total, message))
        return cur.lastrowid, plan


def execute_run(run_id, plan=None, log=print):
    """Check every (pincode, products) group in `plan` (default: everything active)."""
    plan = plan if plan is not None else full_plan()
    with db.connect() as con:
        cities = {r["pincode"]: r["label"] for r in con.execute("SELECT pincode, label FROM pincodes")}
    settings = db.get_settings()
    dmin, dmax = float(settings.get("min_delay", 2)), float(settings.get("max_delay", 5))
    SCREENS.mkdir(parents=True, exist_ok=True)

    status, message = "done", None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=HEADLESS)
            for pin, products in plan:
                if not products:
                    continue
                if _stop_requested(run_id):
                    status, message = "stopped", "Stopped by user"
                    break
                ctx = browser.new_context(viewport={"width": 1400, "height": 900},
                                          locale="en-IN", user_agent=UA)
                page = ctx.new_page()
                # 1) set the pincode once, on the first product page (3 attempts)
                pin_error, on_page = None, None
                for attempt in range(3):
                    try:
                        open_product(page, products[0]["url"])
                        set_location(page, pin, cities.get(pin))
                        on_page, pin_error = products[0]["id"], None
                        break
                    except PincodeNotFound as e:
                        pin_error = str(e)
                        if attempt >= 1:  # two full tries, then believe it
                            break
                    except Blocked:
                        raise
                    except Exception as e:
                        pin_error = f"Could not set location: {type(e).__name__}: {str(e).splitlines()[0][:150]}"
                        log(f"[run {run_id}] {pin}: location attempt {attempt + 1} failed")
                if pin_error:
                    try:
                        page.screenshot(path=str(SCREENS / f"run{run_id}_{pin}_location.png"))
                    except Exception:
                        pass
                # 2) read every product with that location
                for prod in products:
                    res, err = {"status": "ERROR"}, None
                    if pin_error:
                        res, err = {"status": "ERROR", "delivery_text": pin_error}, pin_error
                    else:
                        try:
                            if on_page != prod["id"]:
                                open_product(page, prod["url"])
                            on_page = None
                            res = parse_product_page(page.inner_text("body"))
                        except Blocked:
                            raise
                        except Exception as e:  # keep going with the next product
                            err = f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"
                            res = {"status": "ERROR", "delivery_text": "Check failed"}
                        if res["status"] in ("ERROR", "UNKNOWN"):
                            try:
                                page.screenshot(path=str(SCREENS / f"run{run_id}_{pin}_{prod['id']}.png"))
                            except Exception:
                                pass
                    with db.connect() as con:
                        _save_result(con, run_id, prod, pin, res, err)
                    log(f"[run {run_id}] {pin} · {prod['name']}: {res['status']} {res.get('delivery_text') or ''}")
                    if not pin_error:
                        time.sleep(random.uniform(dmin, dmax))
                ctx.close()
            browser.close()
    except Blocked as e:
        status, message = "failed", f"Blocked: {e}. Try again later or slow down."
    except Exception as e:
        status, message = "failed", f"{type(e).__name__}: {e}"
    with db.connect() as con:
        if status == "done":
            old = con.execute("SELECT message FROM runs WHERE id=?", (run_id,)).fetchone()["message"]
            message = old  # keep the job description ("Re-check: Jaipur …")
        con.execute("UPDATE runs SET status=?, finished_at=?, message=? WHERE id=?",
                    (status, db.now(), message, run_id))
    db.prune()
    log(f"[run {run_id}] finished: {status} {message or ''}")
    return status
