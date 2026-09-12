# Run the examples

Start the stack, apply migrations, then start the workers and scheduler:

```bash
docker compose up --build -d
docker compose exec api alembic upgrade head
docker compose --profile runtime up --build -d
```

Each command prints a run UUID. Open http://localhost:3010 to follow the run,
then select a step to inspect its input, output, and attempts.

| Example | Command | Expected result |
| --- | --- | --- |
| Linear | `docker compose exec api python -m app.examples linear` | fetch → transform → save; final output contains `STRIPE` |
| Parallel | `docker compose exec api python -m app.examples parallel` | Both analyses run independently; combine waits for both and returns score 85 |
| Retry | `docker compose exec api python -m app.examples retry` | request_company fails twice, succeeds on attempt 3, then report completes |
| Worker crash | `docker compose exec api python -m app.examples crash` | A 30-second analysis provides time to kill its worker and observe recovery |

Use `--company Acme` to change the input. The crash example accepts
`--seconds 60` for a longer observation window. Run `python -m app.examples --help`
inside the API container to see options. The CLI submits work; workers and the
scheduler must be running to execute it.

## Kill a worker during analysis

1. Start the crash example and open its run in the dashboard.
2. Select `long_analysis` while it is RUNNING. Find its worker ID in
   `docker compose logs --tail=30 worker-1 worker-2`, matching the run UUID.
3. Kill the owning service: `docker compose kill --signal SIGKILL worker-1`
   (use `worker-2` if it owns the step).
4. Wait for the remaining lease to expire (at most 30 seconds by default), the
   next recovery scan (up to 5 seconds), and the replacement's 30-second handler.
5. The workflow should finish with analysis attempt count 2. Fetch remains at
   attempt 1 and its output is reused.
6. Restart the killed service: `docker compose start worker-1`.

Worker processes register distinct IDs; the Compose service name appears as the
log prefix. `GET http://localhost:8010/workers` shows the killed worker as unhealthy
after its heartbeat times out. Adjust API and frontend ports if configured.

## How they work

Definitions live in the backend so both the CLI and workers import the same code:

- [Linear definition](../backend/app/examples/linear.py)
- [Parallel definition](../backend/app/workers/parallel.py)
- [Retry definition](../backend/app/examples/retry.py)
- [Crash definition](../backend/app/workers/recovery_demo.py)

The linear example's save step returns a record for Relay to persist as output;
it does not write to an external database. The parallel example includes short
sleeps so two workers can visibly overlap.

The retry example uses Redis to simulate a service that rejects its first two
requests. Its counter is scoped to the step's idempotency key and expires after
24 hours without a request. It survives worker restarts during the demo. This
counter is demo state, not Relay's retry counter; retry timing and attempt counts
remain in Postgres. A long interruption past its expiry restarts the simulated
service's failures, so use a fresh run for a new demonstration. No API keys or paid
services are required.

For local Python development, run from `backend/` with its dependencies installed
and `DATABASE_URL`/`REDIS_URL` pointing to reachable services. Do not start the
module from the repository root without adding `backend` to `PYTHONPATH`.

## Reliability checks

Run `docker compose exec api pytest --integration` against the local stack. The
integration fixtures create and remove dedicated test databases and queue keys;
they do not clear application runs. `cd frontend && npm test` covers dependency
ordering and the polling lifecycle.

| Behavior | Automated coverage |
| --- | --- |
| Validate graph structure before creating a run | `test_workflows.py`, `test_workflow_creation.py` |
| Execute once despite duplicate jobs and concurrent claims | `test_worker.py`, `test_leases.py` |
| Run branches concurrently and wait for every prerequisite | `test_parallel_workers.py`, `test_dependencies.py` |
| Persist retry deadlines and enforce retry limits | `test_retries.py`, `test_examples.py` |
| Renew active leases and reject stale attempt results | `test_leases.py` |
| Recover after killing a real worker process | `test_recovery.py` |
| Keep idempotency keys stable through retries and recovery | `test_idempotency.py` |
| Cancel without starting new steps or reviving the run | `test_workflows_api.py` |

These tests validate recovery for steps with committed leases. Postgres state
updates and Redis dispatch are still separate operations. A crash after committing
READY but before enqueueing, or after dequeueing but before claiming, can leave
work undispatched. There is not yet an outbox or a reconciliation scan for these
READY steps. Queue delivery recovery is the next reliability improvement.

Handlers may run again after a lease expires. The lease token fences database
results; it cannot undo external side effects. Use the stable idempotency key with
an external service that actually enforces deduplication. Cancellation likewise
stops future claims without forcibly terminating arbitrary Python code.
