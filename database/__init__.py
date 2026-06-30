"""
database - Workflow Recorder database layer (stdlib sqlite3, zero external deps).
"""
from database.db import init_db, get_db
from database.models import Company, Workflow, WorkflowStep, Snapshot, SnapshotProduct
from database.repository import (
    get_or_create_company, get_company_by_url, get_company_by_id, list_companies,
    get_active_workflow, get_workflow_by_id, list_workflows,
    save_workflow, delete_workflow, rename_workflow, update_step_result,
    save_snapshot, get_previous_snapshot, list_snapshots,
    workflow_exists_for_url, export_workflow_json, import_workflow_json,
)

__all__ = [
    "init_db", "get_db",
    "Company", "Workflow", "WorkflowStep", "Snapshot", "SnapshotProduct",
    "get_or_create_company", "get_company_by_url", "get_company_by_id", "list_companies",
    "get_active_workflow", "get_workflow_by_id", "list_workflows",
    "save_workflow", "delete_workflow", "rename_workflow", "update_step_result",
    "save_snapshot", "get_previous_snapshot", "list_snapshots",
    "workflow_exists_for_url", "export_workflow_json", "import_workflow_json",
]
