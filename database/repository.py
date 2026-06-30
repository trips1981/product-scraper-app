"""
database/repository.py — Data-access helpers for the Workflow Recorder layer.

All DB reads/writes go through these functions.  UI and business-logic modules
never touch raw SQL.  Uses stdlib sqlite3 via database/db.py.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

from database.db import get_db, fetchone, fetchall
from database.models import (
    Company, Snapshot, SnapshotProduct, Workflow, WorkflowStep,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_domain(url: str) -> str:
    try:
        return urlparse(url).netloc or url
    except Exception:
        return url


def _now() -> str:
    return datetime.utcnow().isoformat()


def _products_hash(products: list[dict]) -> str:
    """SHA-256 of sorted (name, url) pairs — stable fingerprint for change detection."""
    key = json.dumps(
        sorted((p.get("name", ""), p.get("link", "")) for p in products),
        sort_keys=True,
    )
    return hashlib.sha256(key.encode()).hexdigest()


def _load_steps(workflow_id: int) -> list[WorkflowStep]:
    rows = fetchall(
        "SELECT * FROM workflow_steps WHERE workflow_id=? ORDER BY step_order",
        (workflow_id,),
    )
    return [WorkflowStep.from_row(tuple(r)) for r in rows]


def _load_products(snapshot_id: int) -> list[SnapshotProduct]:
    rows = fetchall(
        "SELECT * FROM snapshot_products WHERE snapshot_id=?",
        (snapshot_id,),
    )
    return [SnapshotProduct.from_row(tuple(r)) for r in rows]


# ---------------------------------------------------------------------------
# Company
# ---------------------------------------------------------------------------

def get_or_create_company(url: str, name: str = "") -> Company:
    """Return existing Company for this domain or create a new one."""
    domain = _extract_domain(url)
    now    = _now()

    with get_db() as db:
        row = db.execute(
            "SELECT * FROM companies WHERE domain=?", (domain,)
        ).fetchone()

        if row is None:
            cur = db.execute(
                "INSERT INTO companies (domain, base_url, company_name, created_date, last_run) "
                "VALUES (?,?,?,?,?)",
                (domain, url, name or domain, now, now),
            )
            company_id = cur.lastrowid
            logger.info("[repo] new company: %s (id=%s)", domain, company_id)
        else:
            company_id = row["id"]
            new_name   = name if name else row["company_name"]
            db.execute(
                "UPDATE companies SET last_run=?, company_name=? WHERE id=?",
                (now, new_name, company_id),
            )

        row = db.execute(
            "SELECT * FROM companies WHERE id=?", (company_id,)
        ).fetchone()
        return Company.from_row(tuple(row))


def get_company_by_url(url: str) -> Optional[Company]:
    domain = _extract_domain(url)
    row = fetchone("SELECT * FROM companies WHERE domain=?", (domain,))
    return Company.from_row(tuple(row)) if row else None


def get_company_by_id(company_id: int) -> Optional[Company]:
    row = fetchone("SELECT * FROM companies WHERE id=?", (company_id,))
    return Company.from_row(tuple(row)) if row else None


def list_companies() -> list[Company]:
    rows = fetchall("SELECT * FROM companies ORDER BY last_run DESC")
    return [Company.from_row(tuple(r)) for r in rows]


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------

def get_active_workflow(company_id: int) -> Optional[Workflow]:
    """Return the highest-version active workflow for a company, or None."""
    row = fetchone(
        "SELECT * FROM workflows WHERE company_id=? AND status='active' "
        "ORDER BY version DESC LIMIT 1",
        (company_id,),
    )
    if not row:
        return None
    wf       = Workflow.from_row(tuple(row))
    wf.steps = _load_steps(wf.id)
    return wf


def get_workflow_by_id(workflow_id: int) -> Optional[Workflow]:
    row = fetchone("SELECT * FROM workflows WHERE id=?", (workflow_id,))
    if not row:
        return None
    wf       = Workflow.from_row(tuple(row))
    wf.steps = _load_steps(wf.id)
    return wf


def list_workflows(company_id: int) -> list[Workflow]:
    """Return all workflow versions for a company, newest first."""
    rows = fetchall(
        "SELECT * FROM workflows WHERE company_id=? ORDER BY version DESC",
        (company_id,),
    )
    return [Workflow.from_row(tuple(r)) for r in rows]


def save_workflow(
    company_id: int,
    steps: list[dict],
    name: str = "Default Workflow",
    notes: str = "",
) -> Workflow:
    """
    Save steps as a new workflow version.
    Archives the previous active workflow first.
    Returns the newly created Workflow with steps loaded.
    """
    now = _now()

    with get_db() as db:
        # Archive all currently active workflows for this company
        db.execute(
            "UPDATE workflows SET status='archived', updated=? "
            "WHERE company_id=? AND status='active'",
            (now, company_id),
        )

        # Determine next version number
        row = db.execute(
            "SELECT MAX(version) FROM workflows WHERE company_id=?",
            (company_id,),
        ).fetchone()
        next_version = (row[0] or 0) + 1

        # Insert new workflow
        cur = db.execute(
            "INSERT INTO workflows (company_id, version, name, created, updated, status, notes) "
            "VALUES (?,?,?,?,?,?,?)",
            (company_id, next_version, name, now, now, "active", notes),
        )
        wf_id = cur.lastrowid

        # Insert steps
        for i, step_dict in enumerate(steps, 1):
            db.execute(
                "INSERT INTO workflow_steps "
                "(workflow_id, step_order, action_type, target_type, target_value, "
                " selector, page_url, wait_condition, notes) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    wf_id, i,
                    step_dict.get("action_type", "unknown"),
                    step_dict.get("target_type", "auto"),
                    step_dict.get("target_value", ""),
                    step_dict.get("selector"),
                    step_dict.get("page_url"),
                    step_dict.get("wait_condition"),
                    step_dict.get("notes"),
                ),
            )

        logger.info(
            "[repo] saved workflow v%s for company_id=%s (%d steps)",
            next_version, company_id, len(steps),
        )

    return get_workflow_by_id(wf_id)


def delete_workflow(workflow_id: int) -> None:
    with get_db() as db:
        db.execute("DELETE FROM workflows WHERE id=?", (workflow_id,))


def rename_workflow(workflow_id: int, new_name: str) -> None:
    with get_db() as db:
        db.execute(
            "UPDATE workflows SET name=?, updated=? WHERE id=?",
            (new_name, _now(), workflow_id),
        )


def update_step_result(step_id: int, result: str) -> None:
    """Record replay outcome for a step (called by the Player)."""
    with get_db() as db:
        db.execute(
            "UPDATE workflow_steps SET last_result=?, last_run=? WHERE id=?",
            (result, _now(), step_id),
        )


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------

def save_snapshot(
    company_id: int,
    products: list[dict],
    workflow_id: Optional[int] = None,
) -> Snapshot:
    """Persist a product snapshot and return the Snapshot object."""
    content_hash = _products_hash(products)
    now = _now()

    with get_db() as db:
        cur = db.execute(
            "INSERT INTO snapshots (company_id, workflow_id, run_time, product_count, content_hash) "
            "VALUES (?,?,?,?,?)",
            (company_id, workflow_id, now, len(products), content_hash),
        )
        snap_id = cur.lastrowid

        for p in products:
            db.execute(
                "INSERT INTO snapshot_products "
                "(snapshot_id, product_name, product_url, category) VALUES (?,?,?,?)",
                (snap_id, p.get("name", ""), p.get("link", ""), p.get("category")),
            )

        logger.info("[repo] snapshot saved: id=%s products=%d", snap_id, len(products))

    snap          = Snapshot.from_row(
        fetchone("SELECT * FROM snapshots WHERE id=?", (snap_id,))
    )
    snap.products = _load_products(snap_id)
    return snap


def get_previous_snapshot(
    company_id: int,
    exclude_id: Optional[int] = None,
) -> Optional[Snapshot]:
    """Return the most recent snapshot for this company (optionally excluding one)."""
    if exclude_id:
        row = fetchone(
            "SELECT * FROM snapshots WHERE company_id=? AND id!=? "
            "ORDER BY run_time DESC LIMIT 1",
            (company_id, exclude_id),
        )
    else:
        row = fetchone(
            "SELECT * FROM snapshots WHERE company_id=? ORDER BY run_time DESC LIMIT 1",
            (company_id,),
        )
    if not row:
        return None
    snap          = Snapshot.from_row(tuple(row))
    snap.products = _load_products(snap.id)
    return snap


def list_snapshots(company_id: int, limit: int = 20) -> list[Snapshot]:
    rows = fetchall(
        "SELECT * FROM snapshots WHERE company_id=? ORDER BY run_time DESC LIMIT ?",
        (company_id, limit),
    )
    return [Snapshot.from_row(tuple(r)) for r in rows]


# ---------------------------------------------------------------------------
# Convenience checks
# ---------------------------------------------------------------------------

def workflow_exists_for_url(url: str) -> bool:
    company = get_company_by_url(url)
    if not company:
        return False
    wf = get_active_workflow(company.id)
    return wf is not None


# ---------------------------------------------------------------------------
# Import / Export
# ---------------------------------------------------------------------------

def export_workflow_json(workflow_id: int) -> dict:
    """Return a JSON-serialisable dict of a workflow + its steps."""
    wf = get_workflow_by_id(workflow_id)
    if not wf:
        return {}
    return {
        "workflow_id": wf.id,
        "version":     wf.version,
        "name":        wf.name,
        "status":      wf.status,
        "created":     wf.created.isoformat() if wf.created else None,
        "notes":       wf.notes,
        "steps":       [s.to_dict() for s in wf.steps],
    }


def import_workflow_json(company_id: int, data: dict) -> Workflow:
    """Import a previously exported workflow JSON as a new version."""
    return save_workflow(
        company_id=company_id,
        steps=data.get("steps", []),
        name=data.get("name", "Imported Workflow"),
        notes=data.get("notes", ""),
    )
