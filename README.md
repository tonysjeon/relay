# Relay

Relay is a fault-tolerant workflow runtime for long-running AI and backend tasks.
Relay supports workflow definitions, persistent runs, a Redis queue of ready
steps, and multiple workers that execute steps and unlock their dependents.

## Run

Install Docker with Docker Compose, then run from the repository root. The core stack and four local examples require no API keys. Copy the example environment only on first setup:

```bash
cp .env.example .env
docker compose up --build -d --wait
docker compose exec api alembic upgrade head
docker compose --profile runtime up --build -d --wait
docker compose exec api python -m app.examples linear
curl --fail http://localhost:8000/health
```

Expected response: `{"status":"ok"}`. Each request checks PostgreSQL with
`SELECT 1` and Redis with `PING`. An unavailable dependency produces HTTP 503
with `{"status":"unavailable"}`. API docs: http://localhost:8000/docs.

Open [the dashboard](http://localhost:3010) and select the printed run UUID.
The linear example should complete all three steps with one attempt each.

The full stack contains the Next.js dashboard, Python 3.12 / FastAPI, two workers,
a scheduler, PostgreSQL, and Redis. PostgreSQL data persists in a Docker volume.
The API and dashboard bind only to localhost. Set `API_PORT` and `FRONTEND_PORT`
in `.env` if their ports are occupied, and adjust the URLs above. Database and Redis
URLs can be overridden in `.env`; Compose uses internal service hostnames by default.

Start the runtime profile after migrations finish. Without that profile, the API
and dashboard run but submitted workflows wait for workers. The scheduler must
stay running for retries, lost-job delivery, and worker crash recovery.

When updating an existing installation, stop the runtime before migrating:

```bash
docker compose stop worker-1 worker-2 scheduler
docker compose up --build -d --wait
docker compose exec api alembic upgrade head
docker compose --profile runtime up --build -d --wait
```

See [the local verification checklist](examples/local-verification.md) for a clean
startup check and the two-worker crash demo.

## Test

```bash
docker compose exec api pytest
docker compose exec api pytest --integration
```

The default suite runs checks that do not need live services. `--integration`
adds persistence, execution, concurrency, retries, lease recovery, API behavior,
and queue delivery checks against PostgreSQL and Redis. Persistence tests create
and remove a temporary database; the PostgreSQL user needs `CREATEDB` privileges
(the Compose user already has them). Application data is left intact.

GitHub Actions runs two checks on every pull request and push to `main`:

- **Backend tests:** Python 3.12, fresh PostgreSQL 16 and Redis 7 services,
  migration upgrade/schema checks, and the full integration suite.
- **Frontend tests and build:** Node.js 24, a clean lockfile install, tests,
  production build, and type checking.

The jobs run independently and need no repository secrets. New commits cancel
older runs on the same PR. CI can also be started from the Actions tab. To enforce
these checks before merging, add `Backend tests` and `Frontend tests and build`
as required status checks in the repository's branch rules after their first run.

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
docker compose --profile runtime down
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
and ready steps remain in PostgreSQL. Do not call `relay.run` again: the scheduler
will restore their messages once Redis is available.

PostgreSQL commit and Redis enqueue are separate operations. The scheduler repairs
missing `READY` messages every five seconds (`READY_SCAN_INTERVAL_SECONDS`),
including a crash after dequeue but before claim. It only scans active workflows
on its configured queue, visits all eligible steps in batches of 100, and atomically
checks list membership before appending a missing message. Repeated scans do not
build a duplicate backlog while workers are offline. A concurrent claim can still
leave a stale message; conditional database claims safely discard it.

Queue routing is persisted on each workflow. Run the scheduler with `--queue NAME`
for custom queues. Migration `0002` assigns existing runs to `relay:jobs`; operators
with older custom-queue runs should update their routing before starting recovery.
Redis list membership checks are linear in queue length, so this simple reconciler
is intended for modest queues rather than high-throughput dispatch.

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
The scheduler also restores these messages automatically on its next READY scan.

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
and unlocks eligible dependents normally. Each failed attempt retains its own error
in the step attempt history.

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
Redis instance, and queue. Custom queues may share a database: retry, lease, and
READY scans filter by the queue persisted on each workflow.

Inspect the current failure and retry state:

```bash
docker compose exec postgres psql -U relay -d relay -c 'SELECT workflow_run_id, step_name, status, attempt_count, max_attempts, next_retry_at, error FROM step_runs ORDER BY created_at DESC LIMIT 12;'
```

If retry dispatch fails after commit, `QueueDispatchError` identifies the saved
run and ready steps. The READY scan restores their delivery. Continuous schedulers
log transient database or Redis errors and retry later; `--once` exits with an error
so callers can detect a failed scan.
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
a handler that continues after losing its lease. Existing `RUNNING` records created by older, unleased workers
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

## Workflow API

Open `/docs` for the interactive API reference. With `API_PORT=8010`, use
`http://localhost:8010/docs`.

| Endpoint | Result |
| --- | --- |
| `GET /workflows` | Runs, newest first; `limit` (1–100, default 50), `offset`, and optional `status` filter |
| `GET /workflows/{id}` | Run metadata and steps, including inputs, outputs, errors, attempts, leases, and timestamps |
| `GET /workflows/{id}/steps` | The same step list; `depends_on` contains prerequisite step UUIDs |
| `POST /workflows/{id}/cancel` | Cancel a pending or running workflow |
| `GET /workers` | Worker heartbeat health |

For example (adjust the port to match `API_PORT`):

```bash
curl 'http://localhost:8010/workflows?limit=10'
curl 'http://localhost:8010/workflows?status=RUNNING'
curl http://localhost:8010/workflows/RUN_ID
curl http://localhost:8010/workflows/RUN_ID/steps
curl -X POST http://localhost:8010/workflows/RUN_ID/cancel
```

Missing run IDs return 404; malformed IDs and invalid query parameters return
422. Cancelling a completed or failed workflow returns 409. Repeating a
cancellation returns the existing cancelled run without changing its timestamp.

Cancellation prevents new claims, retries, and downstream execution. It does
not interrupt a Python handler already in progress or undo its external effects.
Such a handler can still save its result or error, but the workflow remains
`CANCELLED`. Step statuses retain their execution state, so a cancelled workflow
may contain `READY`, `PENDING`, or `RETRYING` steps that will never execute.
Queued identifiers are discarded when workers see that the run is cancelled.
The run's `completed_at` records when cancellation was accepted, even if a handler
finishes later.

Create runs through `relay.run(...)`; workflow creation over HTTP is not included.

## Dashboard

`docker compose up --build -d` now includes the Next.js dashboard at
`http://localhost:3010` (`FRONTEND_PORT` overrides the port).

The runs page supports status filtering and pagination. Open a run to inspect
its steps in dependency order, then select a step to see its attempts, current
lease owner, timing, latest error, input, and output. Attempt history shows each
claim's worker, start and finish times, outcome, and error, newest first. Failed
handler attempts remain visible after a successful retry; expired leases are
marked ABANDONED when the scheduler recovers them. The dashboard refreshes every
two seconds while visible, preserving the selected
step and the last successful data. Slow requests never overlap. Hidden tabs pause
requests and refresh when visible again. Failed requests show a warning with the
last update time and retry automatically; Refresh also retries immediately. Empty lists and API failures have explicit messages.

For frontend development with the Docker API running:

```bash
cd frontend
npm ci
npm run dev -- --port 3010
```

Stop the Compose frontend first if it already occupies port 3010. The development
server defaults to `API_URL=http://localhost:8010`; set `API_URL` in
`frontend/.env.local` if your backend uses another port. In Compose it uses the
internal API address. Requests are proxied on the Next.js server, so no browser
CORS configuration is required.

Use Node.js 24 for frontend development. Validate with `npm test`,
`npm run build`, and `npm run typecheck` from
`frontend/`. Keep the existing backend integration suite as the execution-state
contract. To manually verify the UI, open a completed run, select each step,
filter to a status with no runs, and check an unknown run URL for the error state.

## Example workflows

See [the example walkthrough](examples/README.md) for linear, parallel, retry,
and worker-crash demonstrations. Start one with:

```bash
docker compose exec api python -m app.examples linear
```

Use `parallel`, `retry`, or `crash` in place of `linear`. Start the Compose
`runtime` profile to run the two workers and scheduler, and follow the printed
run UUID in the dashboard. The retry example fails twice before succeeding on its
third attempt. These four examples run locally without API keys.

## Step attempt history

Migration `0003` adds attempt records for new claims. Workflow detail and steps
responses include an `attempts` array ordered by attempt number. Each record has
`id`, `attempt_number`, `worker_id`, `status`, `started_at`, `completed_at`, and
`error`. Status is RUNNING, COMPLETED, FAILED, or ABANDONED. A failed attempt may
be followed by a retry even though its own outcome stays FAILED.

Attempt writes share the claim, completion, failure, and recovery transactions.
Duplicate deliveries do not create extra attempts, and stale workers cannot change
recorded outcomes. Cancellation preserves the actual outcome of already-running
handlers. ABANDONED finish time records when recovery detected the expired lease,
not an exact worker death time. Keep the scheduler running to resolve expired
RUNNING attempts.

Restart workers and the scheduler after applying migrations. Earlier attempts
cannot be reconstructed and are not backfilled; the dashboard identifies missing
history. Inputs and outputs remain on the step rather than being copied into each
attempt. Deleting a step or workflow also deletes its attempt records.

## Track your own LLM calls

Relay records model calls made by your synchronous step handlers, regardless of
provider. Install and configure your chosen SDK in the worker environment, then
wrap the call explicitly. Relay does not make model requests or need its own API
key. The following adapter-shaped example assumes your application supplies
`model_client` and maps its response fields:

```python
from app import relay

def summarize(ctx):
    prompt = {"text": ctx["workflow_input"]["text"], "instruction": "Summarize"}
    with relay.llm_call(provider="your-provider", model="your-model", input=prompt) as call:
        response = model_client.generate(prompt)
        call.set_result(
            response.text,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )
    return {"summary": response.text}
```

`input` and the result must be JSON-serializable; convert SDK objects to plain
values first. Token counts are optional non-negative integers. Omitted usage is
shown as “Not reported”, not zero. Record a result exactly once before leaving the
context, including `None` for an explicit JSON-null result. Multiple model calls
can be recorded within one attempt.

Open a run, select a step, and expand **LLM calls** within its attempt history to
inspect prompts, responses, provider/model, duration, token counts, and outcome.
Workflow detail and steps API responses include `attempts[].llm_calls` in start
order. Failed calls remain visible after a retry, which creates new records.
Calls interrupted by lease expiry become ABANDONED when recovery runs; late
workers cannot overwrite them. Recorded call duration includes the wrapped code
and ends at recovery detection for abandoned calls, not the exact process death.

Only data explicitly supplied to the wrapper is captured. Omit credentials and
redact sensitive prompt fields before recording: recorded values are stored in
PostgreSQL and exposed through the local API/dashboard. Call errors retain the
exception class. Exceptions propagate normally to your handler and Relay's step
failure handling, whose existing step error may include the exception message.
The wrapper does not intercept SDK internals, subprocesses, or child threads.
Its context is scoped to the executing handler and cleared afterward. For
streaming SDKs, consume the stream and record the final response inside the block.

Apply migration `0004` and restart workers/scheduler before using the wrapper.
Existing attempts have an empty call list; no earlier prompts can be recovered.
Tracking writes are required: a storage failure can fail the step. Model calls
may be billed again on retries or crashes; recording them does not make provider
requests exactly-once. Usage counts describe reported successful results, not a
complete billing ledger. Automatic pricing and provider-specific instrumentation
remain future work.

## Test tracking with OpenAI

Put `OPENAI_API_KEY=your-key` in your local `.env`; never put a key in the prompt,
source code, or chat. `OPENAI_MODEL` defaults to `gpt-4.1-mini`, and
`OPENAI_TIMEOUT_SECONDS` defaults to 60. The model must support the
[Responses API](https://developers.openai.com/api/docs/guides/text).

Rebuild the API, apply migrations, and submit to a dedicated test queue:

```bash
docker compose up --build -d --wait api
docker compose exec api alembic upgrade head
docker compose exec api python -m app.examples openai --queue relay:openai-test --prompt "Explain retries in one sentence."
docker compose exec api python -m app.workers.worker --once --queue relay:openai-test
```

The first Python command prints the run ID; the second consumes one job and exits.
Use a fresh queue name if you have old queued tests. No scheduler or continuously
running workers are needed for this one-step test. If the dashboard has not been
rebuilt for LLM tracking, run `docker compose up --build -d --wait frontend`.

Open the run at http://localhost:3010, select **generate**, and expand its model
call under **Attempt history**. Verify the submitted prompt, returned text,
provider/model, input/output token counts, duration, and COMPLETED status. Raw
output includes the response ID and the model reported by OpenAI.

The test uses one Responses call, a 512-output-token limit, a 4000-character prompt
limit, and no SDK or workflow retries. It incurs OpenAI API usage. Missing keys
fail before submission; API failures appear in attempt history. Refusal, incomplete,
and empty text responses fail the test. Unit/integration tests use an offline HTTP
transport and never make paid calls. The core tracking API remains provider-neutral.
