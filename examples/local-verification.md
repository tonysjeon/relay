# Verify the local stack

Use this checklist before a release. Docker with Compose is sufficient; no API
keys are needed. The examples use the default ports (API 8000, dashboard 3010).
Adjust URLs when using different ports.

## Fresh startup

Use a separate Compose project to verify an empty database without changing your
normal local runs. Choose an unused project name and ports. In one terminal:

```bash
export COMPOSE_PROJECT_NAME=relay-release-check
export API_PORT=18010
export FRONTEND_PORT=13010

docker compose --env-file .env.example up --build -d --wait
docker compose --env-file .env.example exec api alembic upgrade head
docker compose --env-file .env.example exec api alembic current
docker compose --env-file .env.example exec api alembic check
docker compose --env-file .env.example --profile runtime up --build -d --wait
curl --fail http://localhost:18010/health
curl --fail http://localhost:18010/workflows
```

Expect migration `0004 (head)`, no pending schema changes, health `ok`, and an
empty workflow list. Open http://localhost:13010 and confirm the empty state.
The API health check tests connections; it does not verify that migrations have
been applied. Apply migrations before starting workers or submitting examples.

## Execute the examples

Keep using the same terminal and project:

```bash
docker compose --env-file .env.example exec api python -m app.examples linear
docker compose --env-file .env.example exec api python -m app.examples parallel
docker compose --env-file .env.example exec api python -m app.examples retry
```

Select each printed run UUID in the dashboard. Expect:

- Linear: all three steps complete on attempt 1; save output contains `STRIPE`.
- Parallel: both analyses finish before combine starts; final score is 85.
- Retry: request_company succeeds on attempt 3; report completes on attempt 1.

For crash recovery, submit `python -m app.examples crash` through the API container
and follow [the worker-kill walkthrough](README.md#kill-a-worker-during-analysis).
Use the same `--env-file .env.example` and exported project throughout. Kill the
worker that owns RUNNING long_analysis. Expect the other worker to complete it on
attempt 2, with fetch and report each at attempt 1. Restart the killed worker.
Allow about 65 seconds with the default lease and handler durations.

Verify the dashboard refreshes without reloading, keeps the selected step, and
shows the replacement attempt and final output. Check the worker endpoint at
http://localhost:18010/workers for the expired heartbeat and replacement worker.

## Tests and cleanup

```bash
docker compose --env-file .env.example exec api pytest --integration
```

For frontend checks, use Node.js 24 and run `npm ci`, `npm test`, and `npm run build`
from `frontend/`. Integration tests use dedicated databases and queue keys.

Remove only the disposable verification project and its data when finished:

```bash
docker compose --env-file .env.example --profile runtime down --volumes
unset COMPOSE_PROJECT_NAME API_PORT FRONTEND_PORT
```

Do not use `--volumes` on a project whose workflow history you want to retain.
