# Relay

## Current product direction

Relay is a runtime for running and tracking developers' own LLM workflows.
The original V1 established persistence, retries, workers, recovery, and a dashboard
with ordinary Python handlers. That foundation is now implemented.

The next increment is reusable LLM observability: developers wrap their own model
calls, and Relay records request/response data, provider, model, token counts,
timing, and outcome under the responsible step attempt. This is independent of a
particular provider or business workflow. Company research is not a product
requirement.

The original deferred list below is now a pool of future product work. This
increment adds call tracking; MCP, human approvals, scheduling, and other items
will be prioritized separately. This increment also adds token totals and saved text-token cost estimates. Automatic
provider instrumentation and streaming event history remain deferred.

## Overview

Relay is a lightweight, fault-tolerant workflow runtime for long-running AI and backend tasks.

The goal is to let developers define multi-step workflows while Relay handles:

* persistent workflow state
* dependency execution
* asynchronous workers
* retries
* failure recovery
* worker leases / heartbeats
* parallel execution
* basic observability

The core idea is that workflows should survive process or worker failures without restarting from the beginning.

This should feel closer to a small workflow orchestration engine than an LLM wrapper.

---

# 1. Product Goal

A developer should be able to define a workflow such as:

```python
workflow = Workflow("research_company")

workflow.step("fetch", fetch_company)
workflow.step("analyze", analyze_company, depends_on=["fetch"])
workflow.step("report", generate_report, depends_on=["analyze"])

relay.run(workflow, {"company": "Stripe"})
```

Relay should then:

1. create a persistent workflow run
2. determine which steps are ready
3. enqueue ready steps
4. let workers claim and execute them
5. persist outputs and errors
6. unlock dependent steps
7. retry failed work when appropriate
8. recover work if a worker disappears
9. mark the workflow complete when all steps succeed

Do not integrate an LLM until the workflow engine works reliably with normal Python functions.

---

# 2. V1 Scope

Build these features:

* workflow definitions
* dependency graphs
* persistent workflow runs
* persistent step runs
* Redis-backed job queue
* multiple worker processes
* step outputs
* retries with exponential backoff
* worker leases
* worker heartbeat
* abandoned-task recovery
* parallel step execution
* REST API
* basic dashboard
* Docker Compose local environment

Deferred beyond the original V1 (see current product direction above):

* Kubernetes
* multi-region execution
* complex authentication
* billing
* organizations
* distributed tracing
* plugin marketplace
* advanced scheduling
* cron workflows
* visual workflow editor
* MCP support
* human approval steps
* LLM-specific abstractions
* event sourcing

These can come later.

---

# 3. Recommended Stack

Backend:

```text
Python 3.12
FastAPI
SQLAlchemy
PostgreSQL
Redis
Pydantic
```

Frontend:

```text
Next.js
TypeScript
React
```

Infrastructure:

```text
Docker
Docker Compose
```

Testing:

```text
pytest
```

Do not introduce Celery initially.

The point of the project is to implement the worker and orchestration logic ourselves.

---

# 4. Repository Structure

Use a monorepo.

```text
relay/
│
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── core/
│   │   ├── db/
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── services/
│   │   ├── workers/
│   │   └── main.py
│   │
│   ├── tests/
│   ├── requirements.txt
│   └── Dockerfile
│
├── frontend/
│   ├── app/
│   ├── components/
│   ├── lib/
│   └── Dockerfile
│
├── examples/
│   └── basic_workflow.py
│
├── docker-compose.yml
├── README.md
└── .env.example
```

Keep naming simple and readable.

Avoid creating too many abstractions early.

---

# 5. Core Data Model

Start with these tables.

## WorkflowRun

Represents one execution of a workflow.

Fields:

```text
id UUID
workflow_name string
status enum
input JSONB
created_at timestamp
started_at timestamp nullable
completed_at timestamp nullable
```

Statuses:

```text
PENDING
RUNNING
COMPLETED
FAILED
CANCELLED
```

---

## StepRun

Represents one step in one workflow execution.

Fields:

```text
id UUID
workflow_run_id UUID
step_name string
status enum
input JSONB nullable
output JSONB nullable
error text nullable
attempt_count integer
max_attempts integer
next_retry_at timestamp nullable
lease_owner string nullable
lease_expires_at timestamp nullable
created_at timestamp
started_at timestamp nullable
completed_at timestamp nullable
```

Statuses:

```text
PENDING
READY
RUNNING
RETRYING
COMPLETED
FAILED
```

---

## StepDependency

Fields:

```text
id UUID
workflow_run_id UUID
step_run_id UUID
depends_on_step_run_id UUID
```

This lets Relay reconstruct dependencies from the database.

---

## Worker

Fields:

```text
id string
last_heartbeat timestamp
started_at timestamp
```

Keep this simple.

---

# 6. Workflow Definition API

Create lightweight Python classes.

Example:

```python
def fetch_company(ctx):
    return {"company": ctx["company"], "data": "sample"}

def analyze_company(ctx):
    return {"score": 92}

workflow = Workflow("research_company")

workflow.step(
    "fetch",
    fetch_company,
)

workflow.step(
    "analyze",
    analyze_company,
    depends_on=["fetch"],
    retries=3,
)

workflow.step(
    "report",
    generate_report,
    depends_on=["analyze"],
)
```

Recommended internal representation:

```python
class StepDefinition:
    name: str
    handler: Callable
    depends_on: list[str]
    max_attempts: int
```

```python
class Workflow:
    name: str
    steps: dict[str, StepDefinition]
```

Validate:

* duplicate step names
* missing dependencies
* circular dependencies

Add DAG validation before execution.

---

# 7. Execution Context

Each step should receive a context containing:

```python
{
    "workflow_input": {...},
    "step_outputs": {
        "fetch": {...},
        "analyze": {...}
    }
}
```

Do not make steps query the database directly for dependency outputs.

The worker should construct the context before invoking the handler.

Example:

```python
def generate_report(ctx):
    analysis = ctx["step_outputs"]["analyze"]

    return {
        "summary": f"Score: {analysis['score']}"
    }
```

---

# 8. Incremental Development Plan

Build Relay in these phases.

Do not skip ahead.

After each phase:

1. run tests
2. manually verify behavior
3. commit working code
4. do not start the next phase until current functionality works

---

# Phase 1 — Project Setup

Goal:

Get FastAPI, PostgreSQL, Redis, and Docker Compose running.

Tasks:

* initialize backend
* create FastAPI app
* add `/health`
* configure PostgreSQL connection
* configure Redis connection
* create Docker Compose
* add environment config
* add initial pytest setup

Docker Compose should include:

```text
api
postgres
redis
```

Test:

```bash
docker compose up
```

Then:

```text
GET /health
```

should return:

```json
{
  "status": "ok"
}
```

Acceptance criteria:

* backend boots
* Postgres reachable
* Redis reachable
* tests execute successfully

---

# Phase 2 — Database Models

Goal:

Persist workflow execution state.

Implement:

* WorkflowRun
* StepRun
* StepDependency
* Worker

Add migrations.

Create repository/service functions for:

```text
create_workflow_run
create_step_runs
get_workflow_run
get_step_runs
update_step_status
```

Do not put database logic directly inside API routes.

Tests:

* create workflow run
* create multiple step runs
* persist dependencies
* fetch workflow with steps

Acceptance criteria:

A workflow and its step graph can be reconstructed entirely from PostgreSQL.

---

# Phase 3 — Workflow Definition

Goal:

Allow workflows to be defined in Python.

Implement:

```text
Workflow
StepDefinition
```

Features:

* add step
* declare dependencies
* configure retries
* validate dependency names
* detect cycles

Write tests for:

```text
valid linear workflow
parallel workflow
missing dependency
duplicate step
circular dependency
```

Acceptance criteria:

Invalid workflow definitions fail before execution.

---

# Phase 4 — Workflow Creation

Goal:

Convert a Workflow definition into persisted runtime records.

Add:

```python
relay.run(workflow, input)
```

For now, do not execute anything.

It should:

1. validate workflow
2. create WorkflowRun
3. create StepRuns
4. create StepDependencies
5. mark steps with no dependencies as READY
6. return workflow_run_id

Example:

```text
A
↓
B
```

Database:

```text
A = READY
B = PENDING
```

For:

```text
    ┌→ B
A ──┤
    └→ C
```

Database:

```text
A = READY
B = PENDING
C = PENDING
```

Acceptance criteria:

Workflow graph state is correct immediately after creation.

---

# Phase 5 — Redis Job Queue

Goal:

Dispatch READY work asynchronously.

Create a basic Redis queue.

Suggested structure:

```text
relay:jobs
```

A queued job should contain:

```json
{
  "step_run_id": "uuid"
}
```

Create helper functions:

```text
enqueue_step
dequeue_step
```

Do not place full workflow inputs or outputs in Redis.

Redis should carry identifiers only.

PostgreSQL remains the source of truth.

When a workflow is created:

```text
READY step
→ enqueue
```

Acceptance criteria:

Creating a workflow places every initial READY step onto the Redis queue.

---

# Phase 6 — Basic Worker

Goal:

Execute queued steps.

Create:

```bash
python -m app.workers.worker
```

Worker loop:

```text
wait for job
↓
load StepRun
↓
claim step
↓
build execution context
↓
execute handler
↓
store output
↓
mark COMPLETED
```

For the first version, register workflow handlers in-process using a simple registry.

Example:

```python
workflow_registry = {
    "research_company": workflow
}
```

The worker uses:

```text
workflow_name
step_name
```

to locate the Python function.

Do not solve distributed code deployment yet.

Acceptance criteria:

A single worker can execute a simple linear workflow step.

---

# Phase 7 — Dependency Resolution

Goal:

Automatically unlock downstream steps.

After a step completes:

1. find dependent steps
2. check whether all dependencies are COMPLETED
3. change qualifying steps:

```text
PENDING → READY
```

4. enqueue each newly READY step

Important:

Do this transactionally enough to avoid accidentally enqueueing the same step multiple times.

Use a database condition such as:

```text
only update when current status = PENDING
```

Acceptance criteria:

For:

```text
A → B → C
```

Relay automatically executes all three.

---

# Phase 8 — Parallel DAG Execution

Goal:

Support independent branches.

Example:

```text
       ┌→ B ─┐
A ─────┤     ├→ D
       └→ C ─┘
```

Expected:

```text
A completes

B and C both become READY

different workers may execute B and C

D becomes READY only after both complete
```

Start at least two worker processes locally.

Add tests for fan-out and fan-in.

Acceptance criteria:

Parallel branches execute correctly without prematurely unlocking dependent steps.

---

# Phase 9 — Failure Handling

Goal:

Capture errors instead of crashing workers.

Wrap handler execution:

```python
try:
    ...
except Exception as exc:
    ...
```

Persist:

```text
error
attempt_count
status
```

If attempts remain:

```text
RUNNING → RETRYING
```

Otherwise:

```text
RUNNING → FAILED
```

If a required step permanently fails:

```text
workflow → FAILED
```

Do not enqueue downstream steps.

Acceptance criteria:

A failed handler does not kill the worker process.

---

# Phase 10 — Retry Logic

Goal:

Retry transient failures.

Implement exponential backoff.

Suggested:

```text
attempt 1 → 1 second
attempt 2 → 2 seconds
attempt 3 → 4 seconds
attempt 4 → 8 seconds
```

Cap the delay at something reasonable such as 60 seconds.

Store:

```text
next_retry_at
```

Create a small scheduler loop that periodically finds:

```text
status = RETRYING
AND next_retry_at <= now
```

and changes them back to:

```text
READY
```

then enqueues them.

Do not rely on sleeping inside the worker.

Acceptance criteria:

Retry timing persists even if the worker restarts.

---

# Phase 11 — Worker Registration + Heartbeats

Goal:

Know which workers are alive.

On startup:

```text
worker generates worker_id
```

Example:

```text
worker-<hostname>-<uuid>
```

Create Worker row.

Heartbeat every ~10 seconds:

```text
last_heartbeat = now
```

Add:

```text
GET /workers
```

Return:

```json
[
  {
    "id": "...",
    "last_heartbeat": "...",
    "status": "healthy"
  }
]
```

Consider worker unhealthy if heartbeat age exceeds a threshold such as 30 seconds.

Acceptance criteria:

Stopping a worker causes it to eventually show as unhealthy.

---

# Phase 12 — Step Leases

Goal:

Prevent work from becoming permanently stuck.

When a worker claims a READY step:

```text
status = RUNNING
lease_owner = worker_id
lease_expires_at = now + 30 seconds
```

Worker periodically renews the lease while the task is running.

A worker should only successfully claim a step when:

```text
status = READY
```

Use an atomic database update.

Conceptually:

```sql
UPDATE step_runs
SET
  status = 'RUNNING',
  lease_owner = :worker_id,
  lease_expires_at = :expires
WHERE
  id = :step_id
  AND status = 'READY'
```

If zero rows were updated, another worker already claimed it.

Acceptance criteria:

Two workers cannot successfully claim the same READY step.

---

# Phase 13 — Abandoned Task Recovery

Goal:

Recover work when a worker dies.

Create a recovery loop that periodically finds:

```text
status = RUNNING
AND lease_expires_at < now
```

These tasks are abandoned.

For each one:

If retries remain:

```text
attempt_count += 1
status = READY
lease_owner = null
lease_expires_at = null
enqueue
```

Otherwise:

```text
status = FAILED
```

This is one of Relay's most important features.

Create a manual test:

1. start worker A
2. start long-running step
3. kill worker A
4. wait for lease expiration
5. worker B should claim the step
6. workflow should finish

Acceptance criteria:

A workflow survives a worker being killed mid-step.

---

# Phase 14 — Idempotency Support

Goal:

Give steps a stable logical execution key.

Generate something like:

```text
workflow_run_id:step_name
```

Expose it to handlers:

```python
ctx["idempotency_key"]
```

Example:

```python
def charge_customer(ctx):
    external_api.charge(
        amount=100,
        idempotency_key=ctx["idempotency_key"],
    )
```

Do not attempt to magically make arbitrary Python functions idempotent.

Relay should provide the stable key and document that side-effecting integrations should use it when supported.

Acceptance criteria:

Retries receive the same idempotency key.

---

# Phase 15 — REST API

Add:

```text
GET /workflows
GET /workflows/{workflow_run_id}
GET /workflows/{workflow_run_id}/steps
GET /workers
POST /workflows/{workflow_run_id}/cancel
```

Potential response:

```json
{
  "id": "...",
  "workflow_name": "research_company",
  "status": "RUNNING",
  "steps": [
    {
      "name": "fetch",
      "status": "COMPLETED",
      "attempt_count": 1
    },
    {
      "name": "analyze",
      "status": "RUNNING",
      "attempt_count": 2
    },
    {
      "name": "report",
      "status": "PENDING",
      "attempt_count": 0
    }
  ]
}
```

Keep API routes thin.

Execution logic belongs in services.

---

# Phase 16 — Frontend Dashboard

Goal:

Make Relay easy to demonstrate.

Build only three screens initially.

## Runs Page

Display:

```text
Workflow
Status
Created
Duration
```

Example:

```text
research-company    COMPLETED    12.8s
invoice-agent       RUNNING       8.4s
data-pipeline       FAILED       21.7s
```

---

## Workflow Detail Page

Show a simple DAG or ordered step view.

Example:

```text
fetch        ✓ COMPLETED
   ↓
analyze      ↻ RUNNING
   ↓
report       ○ PENDING
```

For parallel workflows:

```text
           ┌→ analyze_reviews ✓
fetch ✓ ───┤
           └→ analyze_news    ↻

                    ↓

                 report ○
```

Do not spend too much time on graph visualization initially.

A clean status layout is enough.

---

## Step Detail

Show:

```text
status
attempt count
worker
started time
completed time
error
output
```

Example:

```text
Analyze Company

Status: RETRYING
Attempts: 2 / 3

Attempt 1:
TimeoutError

Attempt 2:
Worker disconnected
```

---

# Phase 17 — Real-Time Updates

Once the normal dashboard works, add either:

```text
polling every 2 seconds
```

or:

```text
WebSockets
```

Start with polling if it is much simpler.

Only switch to WebSockets after everything else works.

---

# Phase 18 — Example Workflows

Add several examples.

## Simple Linear Workflow

```text
fetch
↓
transform
↓
save
```

---

## Parallel Workflow

```text
            ┌→ analyze_a ─┐
fetch_data ─┤             ├→ combine
            └→ analyze_b ─┘
```

---

## Failure Workflow

A handler intentionally fails twice and succeeds on the third attempt.

Use this to demonstrate retries.

---

## Worker Crash Workflow

A step sleeps for ~30 seconds.

Use it to test:

```text
docker kill worker
```

and lease recovery.

---

# Phase 19 — Tests

Minimum test categories:

## Workflow Validation

```text
valid DAG
missing dependency
cycle detection
duplicate names
```

## Persistence

```text
workflow creation
step creation
dependency creation
status changes
```

## Execution

```text
linear workflow
parallel workflow
fan-in dependency
```

## Failures

```text
handler exception
retry succeeds
retry exhausted
workflow marked failed
```

## Concurrency

```text
two workers cannot claim same step
dependent step only unlocked once
```

## Recovery

```text
expired lease detected
abandoned task requeued
new worker completes task
```

Prioritize integration tests for the orchestration behavior.

---

# 20. Concurrency Rules

Be careful about these cases.

## Duplicate Queue Messages

Redis may contain the same `step_run_id` more than once.

That should be safe.

A worker only executes the step if it can atomically move:

```text
READY → RUNNING
```

Otherwise it discards the duplicate message.

---

## Duplicate Dependency Resolution

Two workers may finish different prerequisite steps at nearly the same time.

Both could decide a downstream step is ready.

Use:

```text
UPDATE
WHERE status = PENDING
```

Only the process that actually transitions it to READY should enqueue it.

---

## Worker Crash After External Side Effect

Relay cannot fully solve this for arbitrary external APIs.

Expose:

```text
idempotency_key
```

and document that side-effecting integrations should use idempotency where available.

---

# 21. Workflow Completion Logic

After every step completion or permanent failure:

Check the entire workflow.

If any required step:

```text
FAILED
```

then:

```text
WorkflowRun = FAILED
```

If all steps:

```text
COMPLETED
```

then:

```text
WorkflowRun = COMPLETED
```

Otherwise:

```text
WorkflowRun = RUNNING
```

Keep this logic centralized.

---

# 22. Logging

Use structured logging.

Include:

```text
workflow_run_id
step_run_id
worker_id
step_name
attempt
```

Example conceptual log:

```text
step_started
workflow_run_id=...
step=analyze
worker=worker-2
attempt=2
```

Do not spend time building an external logging stack yet.

Console logs are enough for V1.

---

# 23. Configuration

Use environment variables.

Example:

```env
DATABASE_URL=
REDIS_URL=
STEP_LEASE_SECONDS=30
WORKER_HEARTBEAT_SECONDS=10
WORKER_TIMEOUT_SECONDS=30
RETRY_SCAN_INTERVAL_SECONDS=2
LEASE_SCAN_INTERVAL_SECONDS=5
```

Provide sensible local defaults.

---

# 24. Docker Compose Target

Eventually local startup should resemble:

```yaml
services:
  api:
  worker-1:
  worker-2:
  scheduler:
  postgres:
  redis:
  frontend:
```

A developer should be able to run:

```bash
docker compose up --build
```

and get the whole project locally.

---

# 25. Killer Demo

This should be treated as a core product requirement.

Create a workflow:

```text
fetch_data
     ↓
long_analysis
     ↓
generate_report
```

Run with two workers.

While:

```text
long_analysis = RUNNING
worker = worker-1
```

kill worker 1.

Expected behavior:

```text
worker-1 dies
↓
heartbeat stops
↓
step lease expires
↓
Relay detects abandoned step
↓
step returns to READY
↓
worker-2 claims it
↓
step completes
↓
generate_report executes
↓
workflow COMPLETED
```

The dashboard should visibly show:

```text
Attempts: 2
```

This is the single most important Relay demo.

---

# 26. README Positioning

Do not describe Relay primarily as an AI application.

Use something like:

> Relay is a fault-tolerant workflow runtime for long-running AI and backend tasks. It persists execution state, schedules dependency-aware work across asynchronous workers, retries transient failures, and recovers abandoned tasks when workers crash.

Then explain that AI agent workflows are one intended use case.

---

# 27. Resume-Oriented Technical Goals

The final project should let us truthfully discuss:

* distributed worker execution
* asynchronous job processing
* DAG scheduling
* persistent state machines
* PostgreSQL transactions
* Redis queues
* concurrency control
* atomic work claiming
* retries and exponential backoff
* worker heartbeats
* task leases
* failure detection
* abandoned-work recovery
* idempotency
* Dockerized services
* REST APIs
* frontend observability

The implementation should favor clean, understandable systems concepts over excessive framework complexity.

---

# 28. Development Rules for Codex

While implementing:

1. Work one phase at a time.
2. Do not build future phases early unless a tiny supporting abstraction is necessary.
3. Before changing code, inspect the existing repository structure.
4. Keep functions reasonably small, but do not over-engineer.
5. Prefer readable Python over complex abstractions.
6. Add tests with each meaningful backend feature.
7. Do not introduce libraries when a small implementation is straightforward.
8. Avoid premature optimization.
9. Do not add AI/LLM integration until the core orchestration runtime is stable.
10. After each phase, summarize:

* files changed
* behavior added
* tests added
* how to run it
* what remains for the next phase

---

# 29. First Task for Codex

Start with **Phase 1 only**.

Set up:

```text
FastAPI backend
PostgreSQL
Redis
pytest
Docker Compose
environment configuration
GET /health
```

The `/health` endpoint should verify that:

```text
API is alive
PostgreSQL is reachable
Redis is reachable
```

Do not create workflow models or worker logic yet.

After Phase 1 works, stop and report what was implemented before continuing.
