"""The latest result for every active product × active pincode.

Used by the local admin app (Overview, Excel export) and by publish.py, which
writes the same data out for the read-only hosted site.
"""
import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

import db

STATUS_LABEL = {
    "DELIVERABLE": "Deliverable",
    "NOT_DELIVERABLE": "Not deliverable",
    "OUT_OF_STOCK": "Out of stock",
    "UNKNOWN": "Unclear",
    "ERROR": "Error",
}
PIN_ORDER = "ORDER BY is_metro DESC, state COLLATE NOCASE, label COLLATE NOCASE, pincode"


def pct(n, d):
    return round(100 * n / d) if d else 0


def build():
    """Everything the coverage views need, computed from the latest results."""
    with db.connect() as con:
        products = [(r["id"], r["name"], r["url"]) for r in con.execute(
            "SELECT id, name, url FROM products WHERE active=1 ORDER BY id")]
        pin_rows = [dict(r) for r in con.execute(f"SELECT * FROM pincodes WHERE active=1 {PIN_ORDER}")]
        rows = [dict(r) for r in con.execute("SELECT * FROM results")]
    pids = {p for p, _, _ in products}
    pins = [r["pincode"] for r in pin_rows]
    pinset = set(pins)
    rows = [r for r in rows if r["product_id"] in pids and r["pincode"] in pinset]
    cells = {(r["pincode"], r["product_id"]): r for r in rows}
    pin_info = {r["pincode"]: {"city": r["label"] or "", "state": r["state"] or "",
                               "metro": bool(r["is_metro"]), "capital": bool(r["is_capital"])} for r in pin_rows}

    counts = {k: 0 for k in STATUS_LABEL}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    checked, ok = len(rows), counts["DELIVERABLE"]
    is_ok = lambda p, q: cells.get((p, q), {}).get("status") == "DELIVERABLE"
    row_ok = {p: sum(1 for q, _, _ in products if is_ok(p, q)) for p in pins}
    col_ok = {q: sum(1 for p in pins if is_ok(p, q)) for q, _, _ in products}
    col_checked = {q: sum(1 for p in pins if (p, q) in cells) for q, _, _ in products}
    by_product = sorted(
        [{"id": q, "name": n, "ok": col_ok[q], "n": col_checked[q], "pct": pct(col_ok[q], col_checked[q])}
         for q, n, _ in products], key=lambda x: (-x["pct"], x["name"]))
    stamps = sorted(r["checked_at"] for r in rows)
    kpi = {
        "coverage": pct(ok, checked), "ok": ok, "checked": checked,
        "full_pins": sum(1 for p in pins if products and row_ok[p] == len(products)),
        "zero_pins": sum(1 for p in pins if row_ok[p] == 0 and any((p, q) in cells for q, _, _ in products)),
        "n_pins": len(pins), "n_products": len(products),
        "full_products": sum(1 for x in by_product if x["n"] and x["ok"] == x["n"]),
        "issues": counts["ERROR"] + counts["UNKNOWN"], "oos": counts["OUT_OF_STOCK"],
        "missing": len(pins) * len(products) - checked,
        "newest": stamps[-1] if stamps else None, "oldest": stamps[0] if stamps else None,
    }
    return {"products": [(q, n) for q, n, _ in products], "urls": {q: u for q, _, u in products},
            "pins": pins, "pin_info": pin_info, "cells": cells, "counts": counts,
            "row_ok": row_ok, "col_ok": col_ok, "rows": rows, "by_product": by_product, "kpi": kpi}


def workbook_bytes(data):
    wb = Workbook()
    ws = wb.active
    ws.title = "Coverage"
    fills = {"DELIVERABLE": "D9F2E1", "NOT_DELIVERABLE": "FBE0E0", "OUT_OF_STOCK": "FDF0D0",
             "UNKNOWN": "ECEEF2", "ERROR": "ECEEF2"}
    ws.append(["Pincode", "City", "State", "Type"] + [n for _, n in data["products"]] + ["Deliverable"])
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1E293B")
        c.alignment = Alignment(wrap_text=True, vertical="center")
    ws.row_dimensions[1].height = 42
    for pin in data["pins"]:
        info = data["pin_info"][pin]
        kind = " & ".join(k for k, v in (("Metro", info["metro"]), ("State capital", info["capital"])) if v) or "Other"
        line = [pin, info["city"], info["state"], kind]
        for pid, _ in data["products"]:
            cell = data["cells"].get((pin, pid))
            if not cell:
                line.append("Not checked")
            elif cell["status"] == "DELIVERABLE":
                line.append(cell["delivery_text"] or "Yes")
            else:
                line.append(STATUS_LABEL.get(cell["status"], cell["status"]))
        line.append(f'{data["row_ok"][pin]}/{len(data["products"])}')
        ws.append(line)
        for j, (pid, _) in enumerate(data["products"]):
            cell = data["cells"].get((pin, pid))
            if cell:
                ws.cell(ws.max_row, 5 + j).fill = PatternFill("solid", fgColor=fills.get(cell["status"], "FFFFFF"))
    for col, w in zip("ABCD", (10, 18, 20, 16)):
        ws.column_dimensions[col].width = w
    for j in range(len(data["products"]) + 1):
        ws.column_dimensions[ws.cell(1, 5 + j).column_letter].width = 18
    ws.freeze_panes = "E2"

    ws3 = wb.create_sheet("By product")
    ws3.append(["Product", "Deliverable pincodes", "Checked", "Coverage %"])
    for c in ws3[1]:
        c.font = Font(bold=True)
    for x in data["by_product"]:
        ws3.append([x["name"], x["ok"], x["n"], x["pct"]])
    ws3.column_dimensions["A"].width = 40

    ws2 = wb.create_sheet("Details")
    cols = [("pincode", "Pincode"), ("product_name", "Product"), ("status", "Status"),
            ("delivery_text", "Delivery"), ("cod", "COD"), ("seller", "Seller"), ("price", "Price (₹)"),
            ("location_text", "Flipkart location"), ("checked_at", "Checked at")]
    ws2.append([h for _, h in cols])
    for c in ws2[1]:
        c.font = Font(bold=True)
    for r in sorted(data["rows"], key=lambda r: (data["pins"].index(r["pincode"]), r["product_id"])):
        ws2.append([{1: "Yes", 0: "No"}.get(r["cod"], "") if k == "cod"
                    else STATUS_LABEL.get(r[k], r[k]) if k == "status" else r[k] for k, _ in cols])
    for col, w in zip("ABCDEFGHI", (10, 36, 16, 22, 6, 14, 10, 40, 19)):
        ws2.column_dimensions[col].width = w
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
