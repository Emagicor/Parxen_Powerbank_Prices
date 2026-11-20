"""Publish the latest results to the read-only hosted site.

Writes site/data/latest.json and site/data/coverage.xlsx (the hosted page reads
only these), then pushes them out, depending on Settings → Publishing:

  git     commit the site/ folder and `git push` (Vercel deploys on every push;
          set the Vercel project's Root Directory to "site")
  vercel  run `vercel deploy --prod` inside site/ (after a one-time `vercel link`)
  off     only write the files

    .venv\\Scripts\\python publish.py      # publish now from the command line
"""
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import db
import snapshot

ROOT = Path(__file__).parent
SITE = ROOT / "site"
OUT = SITE / "data"
_lock = threading.Lock()


def write_files():
    data = snapshot.build()
    OUT.mkdir(parents=True, exist_ok=True)
    by_id = {x["id"]: x for x in data["by_product"]}
    payload = {
        "generated_at": db.now(),
        "kpi": data["kpi"],
        "products": [{"id": q, "name": n, "url": data["urls"][q], "ok": by_id[q]["ok"],
                      "n": by_id[q]["n"], "pct": by_id[q]["pct"]} for q, n in data["products"]],
        "pins": [{"pin": p, **data["pin_info"][p], "ok": data["row_ok"][p]} for p in data["pins"]],
        # cells[pincode][product_id] = [status, delivery text, cod, Flipkart location, checked at]
        "cells": {},
    }
    for (pin, pid), r in data["cells"].items():
        payload["cells"].setdefault(pin, {})[str(pid)] = [
            r["status"], r["delivery_text"] if r["status"] != "ERROR" else None,
            r["cod"], r["location_text"], r["checked_at"]]
    (OUT / "latest.json").write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (OUT / "coverage.xlsx").write_bytes(snapshot.workbook_bytes(data))
    shutil.copyfile(ROOT / "static" / "style.css", SITE / "style.css")  # same look as the admin app
    return payload


def _run(cmd, cwd):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")  # never hang waiting for a password prompt
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=180, env=env)
    return p.returncode, (p.stdout + p.stderr).strip()


def _git_publish():
    if not shutil.which("git"):
        return False, "Git is not installed."
    rc, out = _run(["git", "rev-parse", "--show-toplevel"], ROOT)
    if rc:
        return False, "This folder is not a Git repository yet (see README → Hosted site)."
    _run(["git", "add", "--", "site"], ROOT)
    rc, _ = _run(["git", "diff", "--cached", "--quiet", "--", "site"], ROOT)
    if rc == 0:
        return True, "Nothing changed since the last publish."
    rc, out = _run(["git", "commit", "-m", f"Update coverage snapshot {db.now()[:16]}", "--", "site"], ROOT)
    if rc:
        return False, "git commit failed: " + out.splitlines()[-1][:200]
    rc, out = _run(["git", "push"], ROOT)
    if rc:
        return False, "Committed, but git push failed: " + (out.splitlines() or ["?"])[-1][:200]
    return True, "Pushed to Git; Vercel will deploy in about a minute."


def _vercel_publish():
    exe = shutil.which("vercel")
    if not exe:
        return False, "Vercel CLI not found (npm i -g vercel)."
    if not (SITE / ".vercel").exists():
        return False, "Run `vercel link` inside the site folder once first."
    rc, out = _run([exe, "deploy", "--prod", "--yes"], SITE)
    if rc:
        return False, "vercel deploy failed: " + (out.splitlines() or ["?"])[-1][:200]
    return True, "Deployed with the Vercel CLI."


def publish():
    """Write the snapshot and push it. Returns (ok, message); also saved in settings."""
    with _lock:
        try:
            write_files()
            mode = db.get_settings().get("publish_mode", "off")
            if mode == "git":
                ok, msg = _git_publish()
            elif mode == "vercel":
                ok, msg = _vercel_publish()
            else:
                ok, msg = True, "Files updated in the site folder (publishing is off)."
        except Exception as e:
            ok, msg = False, f"{type(e).__name__}: {e}"
        db.set_settings({"last_publish_at": db.now(), "last_publish_msg": ("" if ok else "Failed: ") + msg})
        return ok, msg


if __name__ == "__main__":
    db.init_db()
    ok, msg = publish()
    print(msg)
    raise SystemExit(0 if ok else 1)
