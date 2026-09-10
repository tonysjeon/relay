# Relay

Relay is a fault-tolerant workflow runtime for long-running AI and backend tasks.
Phases 1 and 2 provide local infrastructure and persistent workflow state.
Workflow execution comes in later phases.

## Run

Install Docker with Docker Compose, then run from the repository root:

```bash
cp .env.example .env
docker compose up --build -d --wait
docker compose exec api alembic upgrade head
curl --fail http://localhost:8000/health
```

Expected response: `{"status":"ok"}`. Each request checks PostgreSQL with
`SELECT 1` and Redis with `PING`. An unavailable dependency produces HTTP 503
with `{"status":"unavailable"}`. API docs: http://localhost:8000/docs.

The stack contains Python 3.12 / FastAPI, PostgreSQL, and Redis. PostgreSQL data
persists in a Docker volume. Only the API is exposed, on localhost; set
`API_PORT` in `.env` if port 8000 is occupied. Database and Redis URLs can be
overridden in `.env`; Compose uses internal service hostnames by default.

## Test

```bash
docker compose exec api pytest
docker compose exec api pytest --integration
```

The default suite checks healthy and failed dependency responses and environment
configuration. `--integration` additionally checks the running services and
persistence, including committed graph reconstruction, transaction rollback,
database constraints, and migration upgrade/downgrade. Persistence tests create
and remove a temporary database; the PostgreSQL user needs `CREATEDB` privileges
(the Compose user already has them). Application data is left intact.

For backend development outside Docker, use Python 3.12:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest
uvicorn app.main:app --reload
```

Set `DATABASE_URL` and `REDIS_URL` to services reachable from your host. Defaults
use localhost. Settings load environment variables and `.env` in the current
directory. Compose does not publish dependency ports to the host.

```bash
docker compose logs api
docker compose down
```

Stopping the stack retains PostgreSQL data.

## Persistence

The schema contains `WorkflowRun`, `StepRun`, `StepDependency`, and `Worker`.
Runs and steps use UUIDs, JSONB inputs/outputs, enum statuses, and timezone-aware
timestamps. Step names are unique within a run. Dependencies must reference
two different steps in the same run, and duplicate edges are rejected.

Run migrations explicitly after starting or updating the stack:

```bash
docker compose exec api alembic upgrade head
docker compose exec api alembic current
docker compose exec api alembic check
```

For local Python development, run the same Alembic commands from `backend/`.
Migrations use `DATABASE_URL`. No tables are created automatically at API startup.

Persistence helpers in `backend/app/services/runs.py` take a SQLAlchemy session
and flush changes; the caller owns the transaction:

```python
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.connections import create_database_engine
from app.services.runs import (
    create_step_dependencies,
    create_step_runs,
    create_workflow_run,
    get_workflow_run,
)

engine = create_database_engine(Settings())
try:
    with Session(engine) as session, session.begin():
        run = create_workflow_run(session, "research", {"company": "Stripe"})
        create_step_runs(session, run.id, ["fetch", "analyze", "report"])
        create_step_dependencies(
            session, run.id, {"analyze": ["fetch"], "report": ["analyze"]}
        )
        run_id = run.id

    with Session(engine) as session:
        stored = get_workflow_run(session, run_id)
        print(stored.workflow_name, [step.step_name for step in stored.steps])
        print([(edge.step_run_id, edge.depends_on_step_run_id)
               for edge in stored.dependencies])
finally:
    engine.dispose()
```

`get_workflow_run` loads both steps and dependency edges and returns `None` for
a missing run. `get_step_runs` returns its steps, or an empty list.
`update_step_status` stores a status and raises `LookupError` for a missing step;
it does not execute work or apply transition, retry, or timestamp rules yet.
Additional model fields can be changed in the same session transaction.

Phase 3 will add Python workflow definitions and DAG validation.
