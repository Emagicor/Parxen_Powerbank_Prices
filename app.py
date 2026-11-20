"""Parxen × Flipkart delivery coverage — web app.

    .venv\\Scripts\\python app.py      → http://localhost:5050
"""
import csv
import io
import re
import threading
import time
from datetime import datetime

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_file, send_from_directory, url_for
import checker
import db
import publish
import snapshot
from snapshot import PIN_ORDER, STATUS_LABEL, pct

app = Flask(__name__)
app.secret_key = "parxen-local-only"

_run_lock = threading.Lock()
_active = {"run_id": None}

# ---------------------------------------------------------------- formatting helpers

def _dt(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


@app.template_filter("when")
def fmt_when(s):
    d = _dt(s)
    if not d:
        return "—"
    today = datetime.now().date()
    day = "Today" if d.date() == today else ("Yesterday" if (today - d.date()).days == 1 else d.strftime("%d %b %Y"))
    return f"{day}, {d.strftime('%H:%M')}"


def duration(a, b):
    da, dbb = _dt(a), _dt(b)
    if not da or not dbb:
        return None
    secs = int((dbb - da).total_seconds())
    if secs < 60:
        return f"{secs}s"
    m = secs // 60
    return f"{m // 60}h {m % 60}m" if m >= 60 else f"{m} min"


@app.template_filter("short_delivery")
def short_delivery(text):
    """'Tomorrow, 11 PM' → 'Tomorrow'; 'Sunday, 22 Nov' → 'Sun 22 Nov'."""
    if not text:
        return ""
    t = text.split(",")
    head = t[0].strip()
    if head.lower() in ("today", "tomorrow"):
        return head
    if len(t) > 1 and re.match(r"^[A-Za-z]+day$", head):
        return f"{head[:3]} {t[1].strip()}"
    return text[:18]


# ---------------------------------------------------------------- run control

def active_run_id():
    rid = _active["run_id"]
    if rid is None:
        return None
    with db.connect() as con:
        row = con.execute("SELECT status FROM runs WHERE id=?", (rid,)).fetchone()
    return rid if row and row["status"] in ("running", "stopping") else None


def start_run(trigger, pairs=None, message=None):
    """Start a job in a background thread: the full grid, or just `pairs`
    [(pincode, product_id), ...]. Returns the run id, or None if one is running."""
    if not _run_lock.acquire(blocking=False):
        return None
    try:
        plan = checker.plan_for(pairs) if pairs is not None else None
        run_id, plan = checker.create_run(trigger, plan, message)
    except Exception:
        _run_lock.release()
        raise
    _active["run_id"] = run_id

    def work():
        try:
            checker.execute_run(run_id, plan=plan)
        finally:
            _run_lock.release()
        if db.get_settings().get("auto_publish", "1") == "1":
            ok, msg = publish.publish()
            print("publish:", msg)

    threading.Thread(target=work, daemon=True, name=f"run-{run_id}").start()
    return run_id


def scheduler_loop():
    while True:
        try:
            s = db.get_settings()
            mode = s.get("schedule", "off")
            if mode in ("daily", "weekly"):
                now = datetime.now()
                hh, mm = (int(x) for x in s.get("schedule_time", "06:00").split(":"))
                due_today = mode == "daily" or now.weekday() == int(s.get("schedule_weekday", "0"))
                today = now.strftime("%Y-%m-%d")
                if (due_today and (now.hour, now.minute) >= (hh, mm)
                        and s.get("last_scheduled_run") != today and active_run_id() is None):
                    if start_run("schedule"):
                        db.set_settings({"last_scheduled_run": today})
        except Exception as e:
            print("scheduler error:", e)
        time.sleep(30)


# ---------------------------------------------------------------- report data

def active_run():
    rid = active_run_id()
    if rid is None:
        return None
    with db.connect() as con:
        return dict(con.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone())


@app.context_processor
def inject():
    return {"STATUS_LABEL": STATUS_LABEL, "active_run": active_run_id(),
            "publish_mode": db.get_settings().get("publish_mode", "off")}


# ---------------------------------------------------------------- pages

@app.route("/")
def dashboard():
    data = snapshot.build()
    run = active_run()
    last = run
    if last is None:
        with db.connect() as con:
            r = con.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        last = dict(r) if r else None
    if last:
        last["duration"] = duration(last["started_at"], last["finished_at"])
    return render_template("report.html", data=data, run=run, last=last)


@app.route("/runs/<int:run_id>")
def run_view(run_id):  # old links
    return redirect(url_for("dashboard"))


@app.route("/runs")
def runs():
    with db.connect() as con:
        rs = [dict(r) for r in con.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 200")]
    for r in rs:
        r["duration"] = duration(r["started_at"], r["finished_at"])
    return render_template("runs.html", runs=rs)


@app.post("/runs/start")
def run_start():
    if start_run("manual", message="Full check") is None:
        flash("A check is already running.", "warn")
    return redirect(url_for("dashboard"))


@app.post("/recheck")
def recheck():
    """Re-check one pincode (all products), one product (all pincodes), every
    failed cell, or every cell that has never been checked."""
    if active_run_id() is not None:
        flash("Another check is running. Try again when it finishes.", "warn")
        return redirect(url_for("dashboard"))
    data = snapshot.build()
    pids = [pid for pid, _ in data["products"]]
    scope = request.form.get("scope")
    if scope == "pin":
        pin = request.form.get("pincode", "")
        pairs = [(pin, pid) for pid in pids] if pin in data["pin_info"] else []
        what = f"{data['pin_info'].get(pin, {}).get('city') or pin} ({pin})"
    elif scope == "product":
        pid = int(request.form.get("product_id", 0))
        pairs = [(pin, pid) for pin in data["pins"]] if pid in pids else []
        what = next((n for q, n in data["products"] if q == pid), "product")
    elif scope == "errors":
        pairs = [k for k, c in data["cells"].items() if c["status"] in ("ERROR", "UNKNOWN")]
        what = "failed checks"
    elif scope == "missing":
        pairs = [(p, q) for p in data["pins"] for q in pids if (p, q) not in data["cells"]]
        what = "unchecked cells"
    else:
        abort(400)
    if not pairs:
        flash("Nothing to re-check.", "info")
    elif start_run("recheck", pairs=pairs, message=f"Re-check: {what}") is None:
        flash("Another check is running. Try again when it finishes.", "warn")
    else:
        flash(f"Re-checking {what}: {len(pairs)} check{'s' if len(pairs) != 1 else ''}.", "ok")
    return redirect(url_for("dashboard"))


@app.post("/runs/<int:run_id>/stop")
def run_stop(run_id):
    with db.connect() as con:
        con.execute("UPDATE runs SET status='stopping' WHERE id=? AND status='running'", (run_id,))
    flash("Stopping after the current pincode finishes.", "info")
    return redirect(url_for("dashboard"))


@app.get("/api/runs/<int:run_id>")
def run_status(run_id):
    with db.connect() as con:
        r = con.execute("SELECT id, status, total, done, message FROM runs WHERE id=?", (run_id,)).fetchone()
    return jsonify(dict(r) if r else {})


@app.get("/export.xlsx")
def export():
    buf = io.BytesIO(snapshot.workbook_bytes(snapshot.build()))
    return send_file(buf, as_attachment=True,
                     download_name=f"parxen_flipkart_coverage_{datetime.now():%Y-%m-%d}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/site/")
@app.get("/site/<path:path>")
def site_preview(path="index.html"):
    """Preview the read-only hosted page exactly as Vercel will serve it."""
    return send_from_directory(publish.SITE, path, max_age=0)


@app.post("/publish")
def publish_now():
    if publish._lock.locked():
        flash("Already publishing.", "info")
    else:
        ok, msg = publish.publish()
        flash(msg, "ok" if ok else "warn")
    return redirect(request.referrer or url_for("settings"))


# ---------------------------------------------------------------- products

@app.route("/products", methods=["GET", "POST"])
def products():
    if request.method == "POST":
        url = request.form.get("url", "").strip()
        name = request.form.get("name", "").strip()
        if "flipkart.com" not in url or "/p/" not in url:
            flash("That doesn't look like a Flipkart product link. It should contain flipkart.com/…/p/…", "warn")
        else:
            if url.startswith("http://"):
                url = "https://" + url[7:]
            with db.connect() as con:
                if con.execute("SELECT 1 FROM products WHERE url=?", (url,)).fetchone():
                    flash("That link is already in the list.", "warn")
                else:
                    slug = url.split("flipkart.com/")[1].split("/p/")[0].replace("-", " ").title()
                    con.execute("INSERT INTO products(name, url, pid, created_at) VALUES (?,?,?,?)",
                                (name or slug, url, db.extract_pid(url), db.now()))
                    flash(f"Added {name or slug}. It will be included in the next check.", "ok")
        return redirect(url_for("products"))
    cov = {}
    with db.connect() as con:
        for r in con.execute("SELECT product_id, SUM(status='DELIVERABLE') ok, COUNT(*) n FROM results "
                             "WHERE pincode IN (SELECT pincode FROM pincodes WHERE active=1) GROUP BY product_id"):
            cov[r["product_id"]] = {"ok": r["ok"], "n": r["n"], "pct": pct(r["ok"], r["n"])}
        items = [dict(r) for r in con.execute("SELECT * FROM products ORDER BY id")]
    return render_template("products.html", items=items, cov=cov, last_run=bool(cov))


@app.post("/products/<int:pid>/<action>")
def product_action(pid, action):
    with db.connect() as con:
        if action == "toggle":
            con.execute("UPDATE products SET active = 1 - active WHERE id=?", (pid,))
        elif action == "delete":
            con.execute("DELETE FROM products WHERE id=?", (pid,))
            con.execute("DELETE FROM results WHERE product_id=?", (pid,))
            flash("Product removed.", "info")
        elif action == "rename":
            name = request.form.get("name", "").strip()
            if name:
                con.execute("UPDATE products SET name=? WHERE id=?", (name, pid))
                flash("Name saved.", "ok")
    return redirect(url_for("products"))


# ---------------------------------------------------------------- pincodes

def _add_pincodes(lines):
    added, skipped, bad = 0, 0, []
    with db.connect() as con:
        for raw in lines:
            parts = [p.strip() for p in raw.replace("\t", ",").split(",")]
            if not parts or not parts[0]:
                continue
            pin = parts[0]
            city = parts[1] if len(parts) > 1 else ""
            state = parts[2] if len(parts) > 2 else ""
            if not db.is_valid_pincode(pin):
                if pin.lower() not in ("pincode", "pin", "pin code"):
                    bad.append(pin)
                continue
            cur = con.execute("INSERT OR IGNORE INTO pincodes(pincode, label, state, created_at) VALUES (?,?,?,?)",
                              (pin, city or None, state or None, db.now()))
            if cur.rowcount:
                added += 1
            else:
                skipped += 1
    return added, skipped, bad


@app.route("/pincodes", methods=["GET", "POST"])
def pincodes():
    if request.method == "POST":
        lines = request.form.get("bulk", "").splitlines()
        f = request.files.get("csv")
        if f and f.filename:
            text = f.read().decode("utf-8-sig", errors="ignore")
            lines += [",".join(row) for row in csv.reader(io.StringIO(text))]
        added, skipped, bad = _add_pincodes(lines)
        msg = f"Added {added} pincode{'s' if added != 1 else ''}."
        if skipped:
            msg += f" {skipped} already in the list."
        if bad:
            msg += f" Ignored {len(bad)} invalid: {', '.join(bad[:8])}{'…' if len(bad) > 8 else ''}"
        flash(msg, "ok" if not bad else "warn")
        return redirect(url_for("pincodes"))
    cov = {}
    with db.connect() as con:
        for r in con.execute("SELECT pincode, SUM(status='DELIVERABLE') ok, COUNT(*) n FROM results "
                             "WHERE product_id IN (SELECT id FROM products WHERE active=1) GROUP BY pincode"):
            cov[r["pincode"]] = {"ok": r["ok"], "n": r["n"]}
        items = [dict(r) for r in con.execute(f"SELECT * FROM pincodes {PIN_ORDER}")]
    counts = {"all": len(items), "metro": sum(1 for i in items if i["is_metro"]),
              "capital": sum(1 for i in items if i["is_capital"]),
              "custom": sum(1 for i in items if not i["is_metro"] and not i["is_capital"])}
    return render_template("pincodes.html", items=items, cov=cov, counts=counts)


@app.post("/pincodes/<int:pid>/<action>")
def pincode_action(pid, action):
    with db.connect() as con:
        if action == "toggle":
            con.execute("UPDATE pincodes SET active = 1 - active WHERE id=?", (pid,))
        elif action == "delete":
            con.execute("DELETE FROM results WHERE pincode=(SELECT pincode FROM pincodes WHERE id=?)", (pid,))
            con.execute("DELETE FROM pincodes WHERE id=?", (pid,))
    return redirect(url_for("pincodes"))


@app.post("/pincodes/delete-all")
def pincodes_delete_all():
    with db.connect() as con:
        con.execute("DELETE FROM pincodes")
        con.execute("DELETE FROM results")
    flash("All pincodes removed.", "info")
    return redirect(url_for("pincodes"))


# ---------------------------------------------------------------- settings

@app.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        f = request.form
        try:
            dmin = max(1.0, float(f.get("min_delay", 2)))
            dmax = max(dmin, float(f.get("max_delay", 5)))
        except ValueError:
            dmin, dmax = 2, 5
        db.set_settings({
            "schedule": f.get("schedule", "off"),
            "schedule_time": f.get("schedule_time", "06:00") or "06:00",
            "schedule_weekday": f.get("schedule_weekday", "0"),
            "min_delay": dmin, "max_delay": dmax,
            "publish_mode": f.get("publish_mode") if f.get("publish_mode") in ("off", "git", "vercel") else "off",
            "auto_publish": "1" if f.get("auto_publish") else "0",
        })
        flash("Settings saved.", "ok")
        return redirect(url_for("settings"))
    with db.connect() as con:
        n_prod = con.execute("SELECT COUNT(*) FROM products WHERE active=1").fetchone()[0]
        n_pin = con.execute("SELECT COUNT(*) FROM pincodes WHERE active=1").fetchone()[0]
    s = db.get_settings()
    avg = (float(s.get("min_delay", 2)) + float(s.get("max_delay", 5))) / 2
    est_min = round(n_pin * (12 + n_prod * (avg + 5)) / 60)
    return render_template("settings.html", s=s, n_prod=n_prod, n_pin=n_pin, est_min=est_min)


if __name__ == "__main__":
    db.init_db()
    db.prune()
    threading.Thread(target=scheduler_loop, daemon=True, name="scheduler").start()
    print("Parxen delivery coverage running at http://localhost:5050")
    app.run(host="127.0.0.1", port=5050, debug=False, threaded=True)
