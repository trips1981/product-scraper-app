"""
database/db.py - SQLite engine for workflow.db using stdlib sqlite3.

Zero external dependencies. WAL mode + foreign keys enabled on every connection.
Thread-safe: uses threading.local() so each thread gets its own connection.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

logger = logging.getLogger(__name__)

_APP_DIR  = Path(__file__).parent.parent
_SNAP_DIR = _APP_DIR / "picker_snapshots"
_SNAP_DIR.mkdir(exist_ok=True)
WORKFLOW_DB = _SNAP_DIR / "workflow.db"

_SCHEMA_SQL = (
    "PRAGMA journal_mode=WAL;"
    "PRAGMA foreign_keys=ON;"
    "PRAGMA synchronous=NORMAL;"
    "CREATE TABLE IF NOT EXISTS companies ("
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "  domain TEXT NOT NULL UNIQUE,"
    "  base_url TEXT NOT NULL DEFAULT '',"
    "  company_name TEXT NOT NULL DEFAULT '',"
    "  created_date TEXT NOT NULL,"
    "  last_run TEXT"
    ");"
    "CREATE TABLE IF NOT EXISTS workflows ("
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "  company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,"
    "  version INTEGER NOT NULL DEFAULT 1,"
    "  name TEXT NOT NULL DEFAULT 'Default Workflow',"
    "  created TEXT NOT NULL,"
    "  updated TEXT NOT NULL,"
    "  status TEXT NOT NULL DEFAULT 'active',"
    "  notes TEXT DEFAULT '',"
    "  UNIQUE(company_id, version)"
    ");"
    "CREATE TABLE IF NOT EXISTS workflow_steps ("
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "  workflow_id INTEGER NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,"
    "  step_order INTEGER NOT NULL,"
    "  action_type TEXT NOT NULL,"
    "  target_type TEXT NOT NULL DEFAULT 'auto',"
    "  target_value TEXT NOT NULL DEFAULT '',"
    "  selector TEXT,"
    "  page_url TEXT,"
    "  wait_condition TEXT,"
    "  notes TEXT,"
    "  last_result TEXT,"
    "  last_run TEXT"
    ");"
    "CREATE TABLE IF NOT EXISTS snapshots ("
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "  company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,"
    "  workflow_id INTEGER REFERENCES workflows(id) ON DELETE SET NULL,"
    "  run_time TEXT NOT NULL,"
    "  product_count INTEGER NOT NULL DEFAULT 0,"
    "  content_hash TEXT"
    ");"
    "CREATE TABLE IF NOT EXISTS snapshot_products ("
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "  snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,"
    "  product_name TEXT NOT NULL,"
    "  product_url TEXT NOT NULL DEFAULT '',"
    "  category TEXT,"
    "  status TEXT"
    ");"
    "CREATE INDEX IF NOT EXISTS ix_companies_domain ON companies(domain);"
    "CREATE INDEX IF NOT EXISTS ix_workflows_company ON workflows(company_id);"
    "CREATE INDEX IF NOT EXISTS ix_workflow_steps_wf ON workflow_steps(workflow_id);"
    "CREATE INDEX IF NOT EXISTS ix_snapshots_company ON snapshots(company_id);"
    "CREATE INDEX IF NOT EXISTS ix_snapshot_products_snap ON snapshot_products(snapshot_id);"
)

_local = threading.local()


def _get_connection() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(
            str(WORKFLOW_DB),
            check_same_thread=False,
            timeout=15,
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        conn.row_factory = sqlite3.Row
        # WAL mode is best but may fail on some network/virtual drives — fall back gracefully
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            logger.warning("[db] WAL mode unavailable, using DELETE journal mode")
            try:
                conn.execute("PRAGMA journal_mode=DELETE")
            except Exception:
                pass
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=NORMAL")
        except Exception:
            pass
        _local.conn = conn
        logger.debug("[db] new SQLite connection (thread=%s)", threading.current_thread().name)
    return conn


def init_db() -> None:
    """Create all tables and indexes if they do not exist. Safe to call multiple times."""
    conn = _get_connection()
    # Strip the PRAGMA lines from schema — already applied in _get_connection
    _ddl_only = [
        s.strip() for s in _SCHEMA_SQL.split(";")
        if s.strip() and not s.strip().upper().startswith("PRAGMA")
    ]
    for stmt in _ddl_only:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as e:
            # IF NOT EXISTS guards make this safe; log anything unexpected
            logger.debug("[db] schema stmt skipped (%s): %.60s", e, stmt)
    conn.commit()
    logger.info("[db] workflow.db schema verified at %s", WORKFLOW_DB)


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Context manager that commits on success, rolls back on error."""
    conn = _get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def fetchone(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    return _get_connection().execute(sql, params).fetchone()


def fetchall(sql: str, params: tuple = ()) -> list:
    return _get_connection().execute(sql, params).fetchall()


def execute(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    return _get_connection().execute(sql, params)
