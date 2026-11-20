"""SQLite storage for products, pincodes, runs and results."""
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "parxen.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    pid TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pincodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pincode TEXT NOT NULL UNIQUE,
    label TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,          -- running / done / stopped / failed
    trigger TEXT NOT NULL,         -- manual / schedule / cli
    total INTEGER NOT NULL DEFAULT 0,
    done INTEGER NOT NULL DEFAULT 0,
    message TEXT
);
CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL,
    product_name TEXT NOT NULL,
    pincode TEXT NOT NULL,
    status TEXT NOT NULL,          -- DELIVERABLE / NOT_DELIVERABLE / OUT_OF_STOCK / UNKNOWN / ERROR / BLOCKED
    delivery_text TEXT,
    cod INTEGER,
    seller TEXT,
    price TEXT,
    location_text TEXT,
    error TEXT,
    checked_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_results_run ON results(run_id);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

DEFAULT_SETTINGS = {
    "schedule": "off",        # off / daily / weekly
    "schedule_time": "06:00",  # HH:MM, local time
    "schedule_weekday": "0",   # 0=Monday … 6=Sunday (weekly only)
    "last_scheduled_run": "",
    "min_delay": "2",          # seconds between product page loads
    "max_delay": "5",
    "schedule_scope": "full",  # full = whole grid each time; batch = the N stalest pincodes
    "batch_size": "100",       # pincodes per batch (about 1.5 min each with 8 products)
    "publish_mode": "off",     # off / git / vercel — how the read-only site gets updated
    "auto_publish": "1",       # publish after every check finishes
    "last_publish_at": "",
    "last_publish_msg": "",
}

# Parxen listings found on Flipkart on 2026-11-20 (edit freely in the app).
SEED_PRODUCTS = [
    ("Parxen 10000 mAh 22.5W", "https://www.flipkart.com/parxen-10000-mah-22-5-w-power-bank/p/itm3bf04885ffa60?pid=PWBHS3WM9BSRSGGC&lid=LSTPWBHS3WM9BSRSGGCHYAGKW&marketplace=FLIPKART"),
    ("Parxen 10000 mAh 22.5W Slim", "https://www.flipkart.com/parxen-10000-mah-22-5-w-slim-power-bank/p/itm403001f622b4f?pid=PWBHP3UUHHZSRSGF&lid=LSTPWBHP3UUHHZSRSGFN0C5OD&marketplace=FLIPKART"),
    ("Parxen 20000 mAh 22.5W (A)", "https://www.flipkart.com/parxen-20000-mah-22-5-w-power-bank/p/itm94352e6bb4eff?pid=PWBHS3WM4VDMCWPJ&lid=LSTPWBHS3WM4VDMCWPJEELPTY&marketplace=FLIPKART"),
    ("Parxen 20000 mAh 22.5W (B)", "https://www.flipkart.com/parxen-20000-mah-22-5-w-power-bank/p/itmcaaca8b6662d6?pid=PWBHS3WM9MYFTABU&lid=LSTPWBHS3WM9MYFTABU7DTFO3&marketplace=FLIPKART"),
    ("Parxen 20000 mAh 22.5W (C)", "https://www.flipkart.com/parxen-20000-mah-22-5-w-power-bank/p/itmdd186a17682a6?pid=PWBHP3UURR2HZHWE&lid=LSTPWBHP3UURR2HZHWECKXYZP&marketplace=FLIPKART"),
    ("Parxen 20000 mAh 65W", "https://www.flipkart.com/parxen-20000-mah-65-w-power-bank/p/itmcad628d631d5c?pid=PWBHS3WMHNHN8Y3N&lid=LSTPWBHS3WMHNHN8Y3NT9VUNU&marketplace=FLIPKART"),
    ("Parxen 10000 mAh 20W Wireless MagSafe", "https://www.flipkart.com/parxen-10000-mah-20-w-wired-wireless-magsafe-compact-pocket-size-power-bank/p/itm904ffd233a57a?pid=PWBHP3UUUYEFXJHZ&marketplace=FLIPKART"),
    ("Parxen 27000 mAh 22.5W","https://www.flipkart.com/parxen-27000-mah-22-5-w-power-bank/p/itm24433951d498f?pid=PWBHS3WMBKSPZKYY&lid=LSTPWBHS3WMBKSPZKYYTPJQWA&marketplace=FLIPKART"),
]

# (pincode, city, state, is_metro, is_capital) — central post-office pincodes,
# checked against India Post on 2026-11-20.
SEED_PINCODES = [
    # Major metros
    ("110001", "New Delhi", "Delhi", 1, 0),
    ("400001", "Mumbai", "Maharashtra", 1, 1),
    ("700001", "Kolkata", "West Bengal", 1, 1),
    ("600001", "Chennai", "Tamil Nadu", 1, 1),
    ("560001", "Bengaluru", "Karnataka", 1, 1),
    ("500001", "Hyderabad", "Telangana", 1, 1),
    ("380001", "Ahmedabad", "Gujarat", 1, 0),
    ("411001", "Pune", "Maharashtra", 1, 0),
    # State capitals
    ("522237", "Amaravati", "Andhra Pradesh", 0, 1),
    ("791111", "Itanagar", "Arunachal Pradesh", 0, 1),
    ("781006", "Dispur", "Assam", 0, 1),
    ("800001", "Patna", "Bihar", 0, 1),
    ("492001", "Raipur", "Chhattisgarh", 0, 1),
    ("403001", "Panaji", "Goa", 0, 1),
    ("382010", "Gandhinagar", "Gujarat", 0, 1),
    ("160017", "Chandigarh", "Punjab & Haryana", 0, 1),
    ("171001", "Shimla", "Himachal Pradesh", 0, 1),
    ("834001", "Ranchi", "Jharkhand", 0, 1),
    ("695001", "Thiruvananthapuram", "Kerala", 0, 1),
    ("462001", "Bhopal", "Madhya Pradesh", 0, 1),
    ("795001", "Imphal", "Manipur", 0, 1),
    ("793001", "Shillong", "Meghalaya", 0, 1),
    ("796001", "Aizawl", "Mizoram", 0, 1),
    ("797001", "Kohima", "Nagaland", 0, 1),
    ("751001", "Bhubaneswar", "Odisha", 0, 1),
    ("302001", "Jaipur", "Rajasthan", 0, 1),
    ("737101", "Gangtok", "Sikkim", 0, 1),
    ("799001", "Agartala", "Tripura", 0, 1),
    ("226001", "Lucknow", "Uttar Pradesh", 0, 1),
    ("248001", "Dehradun", "Uttarakhand", 0, 1),
]
SEED_VERSION = 2


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db(seed=True):
    with connect() as con:
        con.executescript(SCHEMA)
        for k, v in DEFAULT_SETTINGS.items():
            con.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
        if seed and con.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
            for name, url in SEED_PRODUCTS:
                con.execute("INSERT INTO products(name, url, pid, created_at) VALUES (?,?,?,?)",
                            (name, url, extract_pid(url), now()))
        # v2 columns on pincodes (older databases don't have them yet)
        cols = {r["name"] for r in con.execute("PRAGMA table_info(pincodes)")}
        for col, ddl in (("state", "TEXT"), ("is_metro", "INTEGER NOT NULL DEFAULT 0"),
                         ("is_capital", "INTEGER NOT NULL DEFAULT 0"), ("district", "TEXT"),
                         ("is_district", "INTEGER NOT NULL DEFAULT 0")):
            if col not in cols:
                con.execute(f"ALTER TABLE pincodes ADD COLUMN {col} {ddl}")
        # Add / refresh the built-in city list once per seed version, so pincodes a
        # user removes later don't come back on every start.
        have = int(con.execute("SELECT value FROM settings WHERE key='seed_version'").fetchone()[0] or 0) \
            if con.execute("SELECT 1 FROM settings WHERE key='seed_version'").fetchone() else 0
        if seed and have < SEED_VERSION:
            for pin, city, state, metro, capital in SEED_PINCODES:
                con.execute(
                    "INSERT INTO pincodes(pincode, label, state, is_metro, is_capital, created_at) "
                    "VALUES (?,?,?,?,?,?) ON CONFLICT(pincode) DO UPDATE SET "
                    "label=excluded.label, state=excluded.state, is_metro=excluded.is_metro, "
                    "is_capital=excluded.is_capital",
                    (pin, city, state, metro, capital, now()))
            con.execute("INSERT INTO settings(key, value) VALUES ('seed_version', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(SEED_VERSION),))
        # Latest-only: keep just the newest result per (pincode, product).
        con.execute("DELETE FROM results WHERE id NOT IN "
                    "(SELECT MAX(id) FROM results GROUP BY pincode, product_id)")
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_results_cell ON results(pincode, product_id)")
        # A run left "running" means the app was closed mid-run.
        con.execute("UPDATE runs SET status='stopped', message='App was closed during the run' "
                    "WHERE status='running'")


DISTRICT_CSV = Path(__file__).parent / "seed" / "district_hq_pincodes.csv"


def district_rows():
    """One representative pincode per district (India Post directory; see README)."""
    import csv
    with open(DISTRICT_CSV, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def import_districts():
    """Add every district pincode (or tag it if it is already in the list)."""
    added = 0
    with connect() as con:
        for r in district_rows():
            cur = con.execute(
                "INSERT INTO pincodes(pincode, label, state, district, is_district, created_at) VALUES (?,?,?,?,1,?) "
                "ON CONFLICT(pincode) DO UPDATE SET is_district=1, district=excluded.district, "
                "label=COALESCE(pincodes.label, excluded.label), state=COALESCE(pincodes.state, excluded.state)",
                (r["pincode"], r["district"], r["state"], r["district"], now()))
            added += cur.rowcount
    return len(district_rows())


def remove_districts():
    """Remove district pincodes again (metros, capitals and hand-added ones stay)."""
    with connect() as con:
        con.execute("DELETE FROM results WHERE pincode IN (SELECT pincode FROM pincodes "
                    "WHERE is_district=1 AND is_metro=0 AND is_capital=0)")
        n = con.execute("DELETE FROM pincodes WHERE is_district=1 AND is_metro=0 AND is_capital=0").rowcount
        con.execute("UPDATE pincodes SET is_district=0")
    return n


KEEP_RUNS = 25          # job log length
KEEP_SCREENS_DAYS = 7


def prune():
    """Drop old job-log entries that no longer own any result, and old screenshots."""
    with connect() as con:
        con.execute(
            "DELETE FROM runs WHERE status NOT IN ('running','stopping') "
            "AND id NOT IN (SELECT DISTINCT run_id FROM results) "
            "AND id NOT IN (SELECT id FROM runs ORDER BY id DESC LIMIT ?)", (KEEP_RUNS,))
    screens = DB_PATH.parent / "screens"
    if screens.exists():
        cutoff = datetime.now().timestamp() - KEEP_SCREENS_DAYS * 86400
        for f in screens.glob("*.png"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass


# ---------- helpers ----------

PID_RE = re.compile(r"[?&]pid=([A-Z0-9]+)")
PIN_RE = re.compile(r"^[1-9][0-9]{5}$")


def extract_pid(url):
    m = PID_RE.search(url or "")
    return m.group(1) if m else None


def is_valid_pincode(pin):
    return bool(PIN_RE.match(pin or ""))


def get_settings():
    with connect() as con:
        return {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM settings")}


def set_settings(values):
    with connect() as con:
        for k, v in values.items():
            con.execute("INSERT INTO settings(key, value) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))
