# AI Reliability Lab

A platform for testing the reliability of AI agents.

The backend contains typed benchmark/domain models, a deterministic research agent with mock
tools, a scenario runner with fault injection, and PostgreSQL persistence for experiments,
runs, and structured traces, and a layered evaluation engine with rule-based failure diagnosis
and an optional Groq-backed LLM judge, plus versioned experiments with case-level comparison and
configurable regression policies. There is no job queue, API for results, CI workflow, or
dashboard yet.

## Repository layout

| Path                 | Contents                                                       |
| -------------------- | -------------------------------------------------------------- |
| `frontend/`          | Next.js 16 App Router app (TypeScript, Tailwind CSS 4)         |
| `backend/`           | FastAPI service (Python 3.13, managed with uv)                 |
| `docs/engineering/`  | Per-milestone engineering logs                                 |
| `infra/`             | Local infrastructure (`docker-compose.yml`: PostgreSQL 18.6)   |
| `.env.example`       | Template for environment variables (placeholders only)         |

Frontend and backend are independent projects: each has its own toolchain, lockfile, and
commands, and neither needs the other to run or be tested.

## Prerequisites

| Tool    | Version      | Notes                                                        |
| ------- | ------------ | ------------------------------------------------------------ |
| Node.js | 24 LTS       | `frontend/.nvmrc` pins 24.22.0                               |
| npm     | bundled      | ships with Node                                              |
| uv      | tested 0.12.3 | installs Python 3.13 automatically (`backend/.python-version`) |
| Docker  | with Compose v2 | runs the local PostgreSQL (`infra/docker-compose.yml`)       |

## Quick start

```bash
# Database (PostgreSQL on 127.0.0.1:5433)
docker compose -f infra/docker-compose.yml up -d postgres

# Backend: http://localhost:8000/health
cd backend
uv sync --locked
export APP_DATABASE_URL=postgresql+psycopg://reliabilitylab:reliabilitylab_local_dev@127.0.0.1:5433/reliabilitylab
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8000

# Frontend: http://localhost:3000 (in a second terminal)
cd frontend
nvm use
npm ci
npm run dev
```

## Checks

| Check        | Backend (`cd backend`)         | Frontend (`cd frontend`) |
| ------------ | ------------------------------ | ------------------------ |
| Lint         | `uv run ruff check .`          | `npm run lint`           |
| Format       | `uv run ruff format .`         | `npm run format`         |
| Format check | `uv run ruff format --check .` | `npm run format:check`   |
| Type check   | `uv run mypy`                  | `npm run typecheck`      |
| Tests        | `uv run pytest`                | `npm test`               |
| Build        | —                              | `npm run build`          |

Automated tests do not use the network or any external API. Database tests (`backend/tests/db`)
need the local PostgreSQL and are skipped, with the reason shown by `pytest -ra`, if it is not
reachable.

## Environment variables

See `.env.example`. Copy the backend section to `backend/.env` and the frontend section to
`frontend/.env.local`. All variables are currently optional; real `.env` files are
git-ignored.

| Variable           | App      | Default                      | Purpose                                     |
| ------------------ | -------- | ---------------------------- | ------------------------------------------- |
| `APP_SERVICE_NAME` | backend  | `ai-reliability-lab-backend` | Service name reported by `/health`          |
| `APP_ENVIRONMENT`  | backend  | `development`                | `development`, `test`, or `production`      |
| `APP_DATABASE_URL` | backend  | unset                        | PostgreSQL URL (needed for persistence)     |
| `APP_TRACE_RETENTION_DAYS` | backend | `30`                | trace retention window (days)               |
| `APP_JUDGE_PROVIDER` | backend | `none`                    | `groq` enables the optional LLM judge       |
| `APP_GROQ_API_KEY` | backend  | unset                        | Groq key (`GROQ_API_KEY` also accepted)     |
| `APP_GROQ_MODEL`   | backend  | `llama-3.3-70b-versatile`    | Groq model used by the judge                |

## API

`GET /health` returns:

```json
{ "status": "ok", "service": "ai-reliability-lab-backend", "version": "0.1.0", "environment": "development" }
```

More detail: [backend/README.md](backend/README.md), [frontend/README.md](frontend/README.md),
[docs/engineering/](docs/engineering/).
