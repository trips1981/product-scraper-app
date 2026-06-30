"""
picker_ci_monitor.py — CI Snapshot Engine with SQLite history backend.
"""

import os, json, re, sys, sqlite3, hashlib, tempfile, shutil
from datetime import datetime
from difflib import SequenceMatcher
from contextlib import contextmanager

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    # Go up one level from ci/ to product_scraper_app/ so picker_snapshots/
    # is always at the app root, not inside the ci sub-package.
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SNAPSHOT_DIR = os.path.join(BASE_DIR, "picker_snapshots")
os.makedirs(SNAPSHOT_DIR, exist_ok=True)

DB_PATH = os.path.join(SNAPSHOT_DIR, "ci_history.db")
RENAME_SIMILARITY_THRESHOLD = 0.72

# ── DB layer ──────────────────────────────────────────────────────────────────

@contextmanager
def _db():
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_schema():
    with _db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                domain     TEXT NOT NULL,
                saved_at   TEXT NOT NULL,
                item_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS snapshot_items (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
                name        TEXT NOT NULL,
                link        TEXT NOT NULL DEFAULT '',
                hash        TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS change_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                domain      TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                change_type TEXT NOT NULL,
                description TEXT NOT NULL,
                item_name   TEXT,
                item_link   TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_snap_domain  ON snapshots(domain);
            CREATE INDEX IF NOT EXISTS idx_items_snap   ON snapshot_items(snapshot_id);
            CREATE INDEX IF NOT EXISTS idx_items_hash   ON snapshot_items(hash);
            CREATE INDEX IF NOT EXISTS idx_log_domain   ON change_log(domain);
        """)

_ensure_schema()


def _sanitize_domain(domain):
    domain = (domain or "").lower().strip()
    domain = re.sub(r"^www\.", "", domain)
    domain = re.sub(r"[^\w.\-]", "_", domain)
    return domain or "unknown"


def _migrate_json_snapshot(domain):
    """One-time: import legacy .json snapshot into SQLite."""
    json_path = os.path.join(SNAPSHOT_DIR, f"{_sanitize_domain(domain)}.json")
    if not os.path.exists(json_path):
        return
    with _db() as conn:
        if conn.execute("SELECT COUNT(*) FROM snapshots WHERE domain=?", (domain,)).fetchone()[0]:
            return
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        items = raw["items"] if isinstance(raw, dict) and "items" in raw else raw if isinstance(raw, list) else None
        ts    = raw.get("saved_at", datetime.utcnow().isoformat()+"Z") if isinstance(raw, dict) else datetime.utcnow().isoformat()+"Z"
        if items and isinstance(items, list):
            _write_snapshot(domain, items, ts)
    except Exception:
        pass


def _write_snapshot(domain, items, ts=None):
    ts = ts or (datetime.utcnow().isoformat()+"Z")
    with _db() as conn:
        cur = conn.execute(
            "INSERT INTO snapshots (domain, saved_at, item_count) VALUES (?,?,?)",
            (domain, ts, len(items))
        )
        snap_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO snapshot_items (snapshot_id, name, link, hash) VALUES (?,?,?,?)",
            [(snap_id, i.get("name",""), i.get("link",""), i.get("hash","")) for i in items]
        )
    return snap_id


def _load_latest_items(domain):
    _migrate_json_snapshot(domain)
    with _db() as conn:
        row = conn.execute(
            "SELECT id FROM snapshots WHERE domain=? ORDER BY id DESC LIMIT 1", (domain,)
        ).fetchone()
        if not row:
            return None
        rows = conn.execute(
            "SELECT name, link, hash FROM snapshot_items WHERE snapshot_id=?", (row[0],)
        ).fetchall()
    return [{"name": r[0], "link": r[1], "hash": r[2]} for r in rows]


def _record_changes(domain, changes):
    ts = datetime.utcnow().isoformat()+"Z"
    with _db() as conn:
        conn.executemany(
            "INSERT INTO change_log (domain, recorded_at, change_type, description, item_name, item_link)"
            " VALUES (?,?,?,?,?,?)",
            [(domain, ts, c["type"], c["description"], c.get("name"), c.get("link")) for c in changes]
        )


def get_change_history(domain, limit=300):
    with _db() as conn:
        rows = conn.execute(
            "SELECT recorded_at, change_type, description, item_name, item_link"
            " FROM change_log WHERE domain=? ORDER BY id DESC LIMIT ?",
            (domain, limit)
        ).fetchall()
    return [{"recorded_at": r[0], "type": r[1], "description": r[2],
             "name": r[3], "link": r[4]} for r in rows]


def get_all_domains():
    with _db() as conn:
        rows = conn.execute("SELECT DISTINCT domain FROM snapshots ORDER BY domain").fetchall()
    return [r[0] for r in rows]


# ── Public helpers ─────────────────────────────────────────────────────────────

def compute_hash(name, link):
    norm_name = re.sub(r"\s+", " ", (name or "").strip().lower())
    norm_link = (link or "").strip().rstrip("/").lower()
    return hashlib.sha256(f"{norm_name}|{norm_link}".encode()).hexdigest()


def _similarity(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def generate_changes(domain, new_items):
    old_items = _load_latest_items(domain)

    if old_items is None:
        _write_snapshot(domain, new_items)
        _record_changes(domain, [{"type": "BASELINE", "description": "Baseline snapshot created."}])
        return ["Baseline snapshot created."]

    old_map = {i["hash"]: i for i in old_items}
    new_map = {i["hash"]: i for i in new_items}
    added   = [new_map[h] for h in set(new_map) - set(old_map)]
    removed = [old_map[h] for h in set(old_map) - set(new_map)]

    strings, records = [], []
    consumed_added, consumed_removed = set(), set()

    for rem in removed:
        best_s, best_a = 0.0, None
        for add in added:
            if add["hash"] in consumed_added:
                continue
            rb = re.sub(r"\?.*$", "", rem.get("link") or "")
            ab = re.sub(r"\?.*$", "", add.get("link") or "")
            s  = _similarity(rem["name"], add["name"])
            if s >= RENAME_SIMILARITY_THRESHOLD or (rb == ab and s >= 0.45):
                if s > best_s:
                    best_s, best_a = s, add
        if best_a:
            desc = f'RENAMED: "{rem["name"]}" \u2192 "{best_a["name"]}"'
            strings.append(desc)
            records.append({"type": "RENAMED", "description": desc,
                            "name": best_a["name"], "link": best_a.get("link","")})
            consumed_added.add(best_a["hash"])
            consumed_removed.add(rem["hash"])

    for add in added:
        if add["hash"] not in consumed_added:
            desc = f"NEW: {add['name']}"
            strings.append(desc)
            records.append({"type": "NEW", "description": desc,
                            "name": add["name"], "link": add.get("link","")})

    for rem in removed:
        if rem["hash"] not in consumed_removed:
            desc = f"REMOVED: {rem['name']}"
            strings.append(desc)
            records.append({"type": "REMOVED", "description": desc,
                            "name": rem["name"], "link": rem.get("link","")})

    _write_snapshot(domain, new_items)
    if records:
        _record_changes(domain, records)

    return strings or ["No element-level changes detected."]
