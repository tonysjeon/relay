"""Resolve persisted dependencies inside the step completion transaction."""

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session, aliased

from app.models import StepDependency, StepRun, StepStatus


def unlock_dependents(session: Session, step_run_id: UUID) -> list[UUID]:
    """Return only IDs this call transitioned from PENDING to READY.

    The caller holds the workflow row lock, serializing completion transactions
    so simultaneous prerequisite completions cannot miss the final dependency.
    """
    parent = aliased(StepRun)
    incomplete = (
        select(StepDependency.id)
        .join(parent, parent.id == StepDependency.depends_on_step_run_id)
        .where(
            StepDependency.step_run_id == StepRun.id,
            parent.status != StepStatus.COMPLETED,
        )
        .exists()
    )
    candidates = select(StepDependency.step_run_id).where(
        StepDependency.depends_on_step_run_id == step_run_id
    )
    return list(
        session.scalars(
            update(StepRun)
            .where(
                StepRun.id.in_(candidates),
                StepRun.status == StepStatus.PENDING,
                ~incomplete,
            )
            .values(status=StepStatus.READY)
            .returning(StepRun.id)
            .execution_options(synchronize_session=False)
        )
    )
