from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import StepRun, StepStatus, WorkflowRun, WorkflowStatus


def refresh_workflow_status(session: Session, run: WorkflowRun) -> None:
    """Called with the workflow row locked after updating its steps."""
    if run.status in (
        WorkflowStatus.FAILED,
        WorkflowStatus.CANCELLED,
        WorkflowStatus.COMPLETED,
    ):
        return
    statuses = list(
        session.scalars(select(StepRun.status).where(StepRun.workflow_run_id == run.id))
    )
    if StepStatus.FAILED in statuses:
        run.status = WorkflowStatus.FAILED
    elif statuses and all(status == StepStatus.COMPLETED for status in statuses):
        run.status = WorkflowStatus.COMPLETED
    else:
        run.status = WorkflowStatus.RUNNING
        return
    run.completed_at = datetime.now(timezone.utc)
