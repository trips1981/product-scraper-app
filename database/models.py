"""
database/models.py — Dataclass models for the Workflow Recorder layer.

These are plain Python dataclasses — no ORM dependency.
Persistence is handled by database/db.py using stdlib sqlite3.

Tables in workflow.db
---------------------
  companies         — one row per unique domain scraped
  workflows         — versioned workflows per company
  workflow_steps    — ordered semantic steps inside a workflow
  snapshots         — product-list snapshots after each scrape run
  snapshot_products — individual products within a snapshot

Design principles
-----------------
• Semantic, not positional — steps store intent (action_type, target_value),
  NEVER screen coordinates or pixel positions.
• Versioned — every save creates a new version; old versions are archived.
• Zero external deps — stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# ---------------------------------------------------------------------------
# Company
# ---------------------------------------------------------------------------

@dataclass
class Company:
    id:           Optional[int]
    domain:       str
    base_url:     str
    company_name: str
    created_date: datetime
    last_run:     Optional[datetime]

    @classmethod
    def from_row(cls, row: tuple) -> "Company":
        return cls(
            id=row[0], domain=row[1], base_url=row[2],
            company_name=row[3],
            created_date=_dt(row[4]),
            last_run=_dt(row[5]),
        )


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------

@dataclass
class Workflow:
    id:         Optional[int]
    company_id: int
    version:    int
    name:       str
    created:    datetime
    updated:    datetime
    status:     str          # 'active' | 'archived' | 'draft'
    notes:      str
    steps:      list["WorkflowStep"] = field(default_factory=list)

    @classmethod
    def from_row(cls, row: tuple) -> "Workflow":
        return cls(
            id=row[0], company_id=row[1], version=row[2],
            name=row[3], created=_dt(row[4]), updated=_dt(row[5]),
            status=row[6], notes=row[7] or "",
        )


# ---------------------------------------------------------------------------
# WorkflowStep
# ---------------------------------------------------------------------------

# Supported action_type values (open set — recorder can add more):
#   navigate, click_menu, expand_accordion, expand_tree, open_tab,
#   scroll, search, pagination_next, filter_apply, open_product_page,
#   collect_product, wait, ignore_popup, close_cookie_banner,
#   manual_product_selection, hover, tab_switch, browser_back,
#   browser_forward, collect_all_product_urls
#
# Supported target_type values:
#   text, aria_label, css_selector, xpath, role, placeholder,
#   url_pattern, page_position, auto
#
# NEVER store: x/y coordinates, pixel offsets, window dimensions.

@dataclass
class WorkflowStep:
    id:             Optional[int]
    workflow_id:    int
    step_order:     int
    action_type:    str
    target_type:    str    # 'text' | 'aria_label' | 'css_selector' | 'xpath' | 'auto' …
    target_value:   str
    selector:       Optional[str]    # pipe-separated fallback selectors
    page_url:       Optional[str]
    wait_condition: Optional[str]
    notes:          Optional[str]
    last_result:    Optional[str]    # success|failed|skipped|recovered
    last_run:       Optional[datetime]

    @classmethod
    def from_row(cls, row: tuple) -> "WorkflowStep":
        return cls(
            id=row[0], workflow_id=row[1], step_order=row[2],
            action_type=row[3], target_type=row[4], target_value=row[5],
            selector=row[6], page_url=row[7], wait_condition=row[8],
            notes=row[9], last_result=row[10], last_run=_dt(row[11]),
        )

    def to_dict(self) -> dict:
        return {
            "id":             self.id,
            "step_order":     self.step_order,
            "action_type":    self.action_type,
            "target_type":    self.target_type,
            "target_value":   self.target_value,
            "selector":       self.selector,
            "page_url":       self.page_url,
            "wait_condition": self.wait_condition,
            "notes":          self.notes,
            "last_result":    self.last_result,
        }

    @classmethod
    def from_dict(cls, d: dict, workflow_id: int = 0, step_order: int = 0) -> "WorkflowStep":
        return cls(
            id=d.get("id"),
            workflow_id=workflow_id,
            step_order=d.get("step_order", step_order),
            action_type=d.get("action_type", "unknown"),
            target_type=d.get("target_type", "auto"),
            target_value=d.get("target_value", ""),
            selector=d.get("selector"),
            page_url=d.get("page_url"),
            wait_condition=d.get("wait_condition"),
            notes=d.get("notes"),
            last_result=d.get("last_result"),
            last_run=None,
        )


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

@dataclass
class Snapshot:
    id:            Optional[int]
    company_id:    int
    workflow_id:   Optional[int]
    run_time:      datetime
    product_count: int
    content_hash:  Optional[str]
    products:      list["SnapshotProduct"] = field(default_factory=list)

    @classmethod
    def from_row(cls, row: tuple) -> "Snapshot":
        return cls(
            id=row[0], company_id=row[1], workflow_id=row[2],
            run_time=_dt(row[3]), product_count=row[4], content_hash=row[5],
        )


# ---------------------------------------------------------------------------
# SnapshotProduct
# ---------------------------------------------------------------------------

@dataclass
class SnapshotProduct:
    id:           Optional[int]
    snapshot_id:  int
    product_name: str
    product_url:  str
    category:     Optional[str]
    status:       Optional[str]   # unchanged|new|removed|renamed|url_changed|description_updated

    @classmethod
    def from_row(cls, row: tuple) -> "SnapshotProduct":
        return cls(
            id=row[0], snapshot_id=row[1], product_name=row[2],
            product_url=row[3], category=row[4], status=row[5],
        )


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _dt(value) -> Optional[datetime]:
    """Parse ISO datetime string or return None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None
