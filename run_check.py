"""Run one full check from the command line (for Windows Task Scheduler or testing),
then publish the hosted site if publishing is switched on.

    .venv\Scripts\python run_check.py
"""
import sys

import checker
import db
import publish

if __name__ == "__main__":
    db.init_db()
    run_id, plan = checker.create_run("cli", message="Full check")
    status = checker.execute_run(run_id, plan=plan)
    print(f"Run {run_id}: {status}.")
    if db.get_settings().get("auto_publish", "1") == "1":
        print("Publish:", publish.publish()[1])
    sys.exit(0 if status == "done" else 1)
