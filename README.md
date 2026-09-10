# Relay

Relay is a fault-tolerant workflow runtime for long-running AI and backend tasks.
Phase 1 provides local infrastructure. Workflow execution comes in later phases.

## Run

Install Docker with Docker Compose, then run from the repository root:

```bash
cp .env.example .env
docker compose up --build -d --wait
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
configuration. `--integration` additionally checks the running services.

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

Stopping the stack retains PostgreSQL data. Phase 2 adds workflow and step
models, migrations, and persistence operations.
