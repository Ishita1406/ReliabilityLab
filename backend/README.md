# AI Reliability Lab — Backend

FastAPI service for AI Reliability Lab. Managed with [uv](https://docs.astral.sh/uv/);
Python 3.13 is pinned in `.python-version` and dependencies are locked in `uv.lock`.

## Setup

```bash
cd backend
uv sync --locked          # creates .venv with runtime + dev dependencies from uv.lock
cp ../.env.example .env   # optional; defaults work without it
```

`uv` downloads Python 3.13 automatically if it is not already installed.

## Development

```bash
uv run uvicorn app.main:app --reload --port 8000
curl http://localhost:8000/health
```

Interactive API docs are served at <http://localhost:8000/docs>.

## Quality checks

| Purpose     | Command                     |
| ----------- | --------------------------- |
| Lint        | `uv run ruff check .`       |
| Format      | `uv run ruff format .`      |
| Format check| `uv run ruff format --check .` |
| Type check  | `uv run mypy`               |
| Tests       | `uv run pytest`             |

## Configuration

Settings are read from environment variables prefixed with `APP_` (and from `backend/.env`
if present). See `src/app/config.py` and the root `.env.example`.

| Variable           | Default                       | Allowed values                        |
| ------------------ | ----------------------------- | ------------------------------------- |
| `APP_SERVICE_NAME` | `ai-reliability-lab-backend`  | any string                            |
| `APP_ENVIRONMENT`  | `development`                 | `development`, `test`, `production`   |

## Layout

```
src/app/
  main.py        # create_app() factory and ASGI `app`
  config.py      # pydantic-settings Settings
  api/health.py  # GET /health
tests/           # pytest suite (no network, no external services)
```
