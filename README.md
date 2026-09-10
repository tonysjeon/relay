# Relay

Relay is a fault-tolerant workflow runtime for long-running AI and backend tasks.
Phases 1–4 provide local infrastructure, workflow definitions, and persistent runs.
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

## Workflow definitions

Define workflows in Python with `app.workflows.Workflow`:

```python
from app.workflows import Workflow

def fetch(ctx):
    return {"company": ctx["workflow_input"]["company"]}

def analyze(ctx):
    return {"company": ctx["step_outputs"]["fetch"]["company"], "score": 92}

workflow = Workflow("research_company")
workflow.step("fetch", fetch)
workflow.step("analyze", analyze, depends_on=["fetch"], retries=3)
workflow.validate()
```

`step()` returns an immutable `StepDefinition`; `workflow.steps` exposes a
read-only mapping in declaration order. Dependencies are copied into tuples.
`retries` counts additional attempts: the default `0` gives `max_attempts=1`,
and `retries=3` gives `max_attempts=4`.

Duplicate names and invalid configuration values raise `ValueError` immediately.
Non-callable handlers and a bare string instead of a dependency list raise `TypeError`.
Dependencies may reference steps declared later. Call `validate()` after defining
the full graph: it rejects empty workflows, missing dependencies, and cycles,
including self-dependencies. Independent roots and parallel branches are valid.
Validation never invokes handlers or accesses PostgreSQL or Redis.

Run definition tests without Docker from `backend/` with
`pytest tests/test_workflows.py`.

## Create a workflow run

After applying migrations, use the Python entry point to persist a definition:

```python
from app import relay

# Using the workflow defined above:
run_id = relay.run(workflow, {"company": "Stripe"})
print(run_id)
```

`relay.run` validates the graph before accessing PostgreSQL, then commits the
workflow, all steps, and dependency edges in one transaction. It returns a UUID
only after the transaction commits. Errors propagate and roll back the entire
creation. Each call creates a separate run, even for the same definition/input.

The workflow starts `PENDING`. Steps without dependencies start `READY`; other
steps start `PENDING`. Retry limits are copied from the definitions, attempt
counts start at zero, and outputs and execution timestamps remain unset.
The input is stored on the workflow; per-step context is constructed in a later
phase. No handlers execute and no Redis jobs are queued yet.

By default the entry point uses `DATABASE_URL` and disposes its engine afterward.
An application can supply a reusable SQLAlchemy engine with
`relay.run(workflow, input, engine=engine)`; it retains ownership of that engine.
Use `get_workflow_run` from the persistence example to inspect the saved graph.

Run `docker compose exec api pytest --integration` to test creation, initial
states, independent runs, and transaction rollback against PostgreSQL.
Phase 5 adds Redis queueing for ready steps.
