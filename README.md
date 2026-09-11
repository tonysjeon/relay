# Relay

Relay is a fault-tolerant workflow runtime for long-running AI and backend tasks.
Relay supports workflow definitions, persistent runs, a Redis queue of ready
steps, and multiple workers that execute steps and unlock their dependents.

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
workflow, all steps, and dependency edges in one transaction. After commit, it
enqueues all root step IDs in Redis and returns the run UUID. Database errors
roll back the entire creation. Each call creates a separate run, even for the
same definition/input. Queue failures have separate semantics described below.

The workflow starts `PENDING`. Steps without dependencies start `READY`; other
steps start `PENDING`. Retry limits are copied from the definitions, attempt
counts start at zero, and outputs and execution timestamps remain unset.
The input is stored on the workflow; the worker constructs per-step context when
it claims a job. Creation itself does not execute handlers.

By default the entry point uses `DATABASE_URL` and disposes its engine afterward.
An application can supply a reusable SQLAlchemy engine with
`relay.run(workflow, input, engine=engine)`; it retains ownership of that engine.
Use `get_workflow_run` from the persistence example to inspect the saved graph.

Run `docker compose exec api pytest --integration` to test creation, initial
states, independent runs, and transaction rollback against PostgreSQL.

## Redis job queue

The FIFO list `relay:jobs` contains only messages shaped like
`{"step_run_id": "uuid"}`. Workflow input and outputs stay in PostgreSQL.
Creation uses one `RPUSH` to append every initial ready step after the database
commit, so queued IDs refer to committed records. Dependent steps are not queued.

`app.services.queue` exposes `enqueue_step(redis, step_run_id)`,
`enqueue_steps(redis, step_run_ids)`, and `dequeue_step(redis)`. Dequeue uses
nonblocking `LPOP` and returns a UUID or `None` for an empty queue. A malformed
message is removed and raises `ValueError`. Optional `queue_name` overrides the
default key on these helpers and on `relay.run` (useful for isolated tests).

`relay.run` uses `REDIS_URL` by default and closes the client afterward. Supply
`redis=client` to reuse a caller-owned connection. Redis errors after commit raise
`relay.QueueDispatchError`, containing `run_id` and `step_run_ids`. The saved run
and ready steps remain in PostgreSQL. After Redis recovers, retry dispatch with
`enqueue_steps(client, error.step_run_ids)` rather than calling `relay.run` again.

PostgreSQL commit and Redis enqueue are separate operations. A process crash
between them can leave ready steps unqueued; there is no automatic
reconciliation. An uncertain Redis response may mean a retry creates duplicate
messages. Atomic work claiming prevents concurrent duplicate execution.
Dequeue removes a job. Once a worker commits its claim, its lease enables recovery
if it disappears. A crash between dequeue and claim can still lose that queue
message; recovery of unclaimed `READY` jobs is not implemented.

Inspect without consuming jobs:

```bash
docker compose exec redis redis-cli LLEN relay:jobs
```

Use `docker compose exec redis redis-cli LRANGE relay:jobs 0 -1` to see IDs.
## Basic worker

The worker looks up Python handlers by persisted workflow name and step name in
`backend/app/workers/registry.py`. Producers and workers must use the same
definitions. The registry includes a small `basic` workflow with `fetch → report`.

With the stack running and migrations applied, create a run and consume one job:

```bash
docker compose exec api python -c 'from app import relay; from app.workers.registry import basic_workflow; print(relay.run(basic_workflow, {"company": "Stripe"}))'
docker compose exec api python -m app.workers.worker --once
```

Inspect persisted step states and output:

```bash
docker compose exec postgres psql -U relay -d relay -c 'SELECT workflow_run_id, step_name, status, output FROM step_runs ORDER BY created_at, step_name;'
```

The `fetch` step becomes `COMPLETED` with `{"company": "Stripe"}`. `report` becomes
`READY` and is automatically queued. Run the `--once` command again to execute
`report`, or use the continuous worker to execute both. The workflow becomes
`RUNNING` on the first claim and `COMPLETED` when all its steps succeed.

Run continuously with `docker compose exec api python -m app.workers.worker`, or
run `python -m app.workers.worker` from `backend/` using host-accessible database
and Redis URLs. The idle loop polls every half second. Ctrl-C closes connections.
`--once` consumes at most one message and exits, including when the queue is empty;
`--queue NAME` selects a different Redis list. The optional Compose runtime profile
below runs two workers and a scheduler in separate containers.

Before executing, the worker resolves a synchronous handler and checks persisted
prerequisites. It atomically claims a `READY` step, increments its attempt count,
and commits `RUNNING` before invoking user code. No database transaction is held
while the handler runs. Context contains `workflow_input` and direct dependency
`step_outputs`; it is also saved as the step input. JSON-serializable output and
completion time are persisted before the step becomes `COMPLETED`.

Duplicate messages for already claimed/completed steps and messages for missing
steps are skipped. Terminal workflows are not executed. Logs include event,
workflow/step IDs, step name, and attempt count.

Handler exceptions and non-JSON outputs are persisted without stopping the worker.
Steps with attempts remaining become `RETRYING`; exhausted steps become `FAILED`
and fail the workflow. Registry, queue, or database errors still propagate. A
process crash or infrastructure failure can leave a claimed step `RUNNING` until
its lease expires and the scheduler recovers it. Handlers must be synchronous and
return JSON-compatible values (including `None`).

Run `docker compose exec api pytest --integration` for worker execution,
dependency context, output persistence, duplicate claims, and failure-boundary
tests.

## Dependency resolution

Completing a step saves its output and changes eligible dependents from `PENDING`
to `READY` in the same database transaction. All persisted prerequisites must be
`COMPLETED`; only newly transitioned step IDs are dispatched to Redis after commit.
Consequently a continuous worker can execute `A → B → C` without manual updates.

Completion transactions briefly lock the workflow row. This ensures concurrent
prerequisite completions see one another's results. The `PENDING` condition
prevents duplicate readiness transitions; the lock does not cover handler execution.

If Redis dispatch fails, the completed step stays completed and its dependents
stay ready. `QueueDispatchError` exposes the run ID and newly ready IDs for retry
with `enqueue_steps`; retry dispatch without rerunning the completed handler.
The existing commit-to-queue crash window remains, with no automatic reconciliation.

Workflow status is updated in the same transaction: permanent failure fails the
run, and all steps succeeding completes it. Failed/cancelled runs are never revived.

## Run parallel branches

The registry also includes `parallel`: `fetch` unlocks `analyze_market` and
`analyze_reviews`, and `combine` waits for both. The example branches pause for
two and three seconds so their overlap is easy to observe. Their outputs include
process IDs to identify which worker executed each branch.

Start the continuous worker in **two separate terminals**:

```bash
docker compose exec api python -m app.workers.worker
```

In a third terminal, create a fresh run and note the printed UUID:

```bash
docker compose exec api python -c 'from app import relay; from app.workers.parallel import parallel_workflow; print(relay.run(parallel_workflow, {"company": "Stripe"}))'
```

Both workers share the queue. Expect `fetch` to finish first, the two analysis
steps to run in different processes at overlapping times, and `combine` to start
only after both finish. Its output should be `{"company": "Stripe", "score": 85.0}`.
The workflow row becomes `COMPLETED` after the final step succeeds.

Inspect step output and execution times (filter by the printed `workflow_run_id`
when inspecting a particular run):

```bash
docker compose exec postgres psql -U relay -d relay -c 'SELECT workflow_run_id, step_name, status, attempt_count, started_at, completed_at, output FROM step_runs ORDER BY created_at DESC, step_name LIMIT 12;'
```

Stop each worker with Ctrl-C after the run finishes. Each process registers a
unique worker ID; `/workers` reports its heartbeat and current health.

`docker compose exec api pytest --integration` includes process-level tests with
controlled branch release: both completion orders, overlapping execution, no
premature join, and duplicate branch messages executing only once. Each test uses
an isolated queue and temporary database and stops its worker processes afterward.

## Failure handling and retries

Set `retries` when defining a step. It counts additional attempts, so `retries=2`
allows three handler invocations. The worker increments `attempt_count` when it
claims work, saves the exception type/message on failure, and keeps dependent
steps blocked. Exhausting attempts marks both the step and workflow `FAILED`.
A successful retry saves output, clears the latest error and retry deadline,
and unlocks eligible dependents normally. Error history is not stored yet.

Retry deadlines are persisted in `next_retry_at`, with delays of 1, 2, 4, 8, 16,
32, then 60 seconds. Workers do not sleep for retries. Run a scheduler alongside
the worker, in another terminal:

```bash
docker compose exec api python -m app.workers.scheduler
```

The scheduler scans every two seconds by default, so dispatch occurs on the first
scan at or after the deadline. Set `RETRY_SCAN_INTERVAL_SECONDS` in `.env` and
recreate the API container to change it. `--once` performs one scan;
`--queue NAME` selects the destination Redis list. A scheduler restart reads the
existing database deadlines. Multiple schedulers use workflow locks and conditional
updates to avoid dispatching the same retry twice.

The retry scan handles due `RETRYING` steps on `RUNNING` workflows. It scans
up to 100 eligible workflows per iteration and preserves attempt counts until a
worker claims the next attempt. Scheduler and worker must use the same database,
Redis instance, and queue. When using custom queues, use a separate database per
queue: queue routing is not stored on workflow records.

Inspect the current failure and retry state:

```bash
docker compose exec postgres psql -U relay -d relay -c 'SELECT workflow_run_id, step_name, status, attempt_count, max_attempts, next_retry_at, error FROM step_runs ORDER BY created_at DESC LIMIT 12;'
```

If retry dispatch fails after commit, `QueueDispatchError` identifies the saved
run and ready steps for manual dispatch retry. The database-to-Redis crash window
still applies; the scheduler does not automatically recover already `READY` jobs.
Use Ctrl-C to stop the scheduler. Run `docker compose exec api pytest --integration`
to verify failure isolation, retry timing, exhaustion, and scheduler restarts.

## Worker heartbeats and crash recovery

Workers register as `worker-<hostname>-<uuid>` and update their heartbeat every
10 seconds, including while handlers run. `GET /workers` reports IDs, timestamps,
and `healthy`/`unhealthy` status; stopped workers become unhealthy after 30 seconds
by default. Historical worker rows remain available for inspection.

A claim sets a 30-second lease and records its worker and attempt. A background
thread renews the lease every third of its duration. Renewal, completion, and
failure writes must match the current owner and attempt and have an unexpired
lease. Late results from an old attempt cannot overwrite a replacement's state.
Database time is used for lease expiry checks.

The scheduler checks expired leases every five seconds. If attempts remain, it
clears the lease, marks the step `READY`, and queues its ID after commit. Otherwise
it marks the step and workflow `FAILED`. Attempts count actual claims: a killed
first execution followed by a replacement claim has `attempt_count=2`, not 3.
Worker health is informational; the step lease determines whether work is abandoned.

Configuration in `.env`:

```env
WORKER_HEARTBEAT_SECONDS=10
WORKER_TIMEOUT_SECONDS=30
STEP_LEASE_SECONDS=30
LEASE_SCAN_INTERVAL_SECONDS=5
```

Keep the heartbeat interval comfortably below the worker timeout. Restart workers
and the API after changing configuration. Retry and lease scans have independent
intervals; scheduler `--once` performs both scans.

After starting the base stack and applying migrations, start two workers and
the scheduler:

```bash
docker compose --profile runtime up --build -d
docker compose logs -f worker-1 worker-2 scheduler
```

In another terminal, submit a workflow with a 30-second analysis step:

```bash
docker compose exec api python -c 'from app import relay; from app.workers.recovery_demo import recovery_workflow; print(relay.run(recovery_workflow, {"company": "Stripe"}))'
```

When logs show `long_analysis` starting, kill the service that owns it. Substitute
`worker-2` if that service claimed the step:

```bash
docker compose kill --signal SIGKILL worker-1
```

After lease expiry and the next scan, the surviving worker should execute
`long_analysis` on attempt 2, then `report`. Allow another 30 seconds for the
replacement analysis itself. All steps and the workflow should become `COMPLETED`.
Inspect statuses, owners, attempts, and outputs using the returned run UUID:

```bash
docker compose exec postgres psql -U relay -d relay -c 'SELECT workflow_run_id, step_name, status, attempt_count, lease_owner, lease_expires_at, output FROM step_runs ORDER BY created_at DESC, step_name LIMIT 12;'
```

Restart a killed service with `docker compose start worker-1`. Stop the runtime
services with `docker compose stop worker-1 worker-2 scheduler`.

Lease fencing protects database state; it cannot undo external side effects from
a handler that continues after losing its lease. The database-to-Redis dispatch
window also remains. Existing `RUNNING` records created by older, unleased workers
have no expiry and require manual inspection; restart old worker processes before
using recovery. The test suite includes real process termination, lease renewal,
stale-result rejection, concurrent recovery scans, and retry exhaustion.

## Handler idempotency

Every handler receives `ctx["idempotency_key"]`, formatted as
`<workflow_run_id>:<step_name>`. It stays the same across handler retries and
worker recovery, and differs for other steps and new runs. Relay also saves the
key in the step's execution input.

Pass this key to external services that support idempotency, for example
`payments.charge(..., idempotency_key=ctx["idempotency_key"])`. Relay may execute a
handler more than once; the key alone cannot prevent duplicate side effects.
The external service must enforce deduplication, or your integration must store
and check the key transactionally with its side effect.
