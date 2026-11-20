"""Run one check from the command line (for Windows Task Scheduler or testing),
then publish the hosted site if publishing is switched on.

Follows Settings → "Each automatic check covers": every pincode, or the next
rolling batch of the stalest pincodes.

    .venv\\Scripts\\python run_check.py           # as configured
    .venv\\Scripts\\python run_check.py --full    # every pincode
"""
import sys

import checker
import db
import publish
import snapshot

if __name__ == "__main__":
    db.init_db()
    s = db.get_settings()
    if s.get("schedule_scope") == "batch" and "--full" not in sys.argv:
        pairs, n = snapshot.next_batch(int(s.get("batch_size", 100)))
        run_id, plan = checker.create_run("cli", checker.plan_for(pairs), f"Batch: {n} stalest pincodes")
    else:
        run_id, plan = checker.create_run("cli", message="Full check")
    status = checker.execute_run(run_id, plan=plan)
    print(f"Run {run_id}: {status}.")
    if s.get("auto_publish", "1") == "1":
        print("Publish:", publish.publish()[1])
    sys.exit(0 if status == "done" else 1)
