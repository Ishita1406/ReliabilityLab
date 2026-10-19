# AI Reliability Lab

A platform for exercising AI agents against versioned benchmarks, retaining execution traces,
evaluating outcomes, detecting regressions, and collecting human review.

## Repository layout

| Path                 | Contents                                                       |
| -------------------- | -------------------------------------------------------------- |
| `frontend/`          | Next.js 16 App Router app (TypeScript, Tailwind CSS 4)         |
| `backend/`           | FastAPI service (Python 3.13, managed with uv)                 |
| `docs/engineering/`  | Per-milestone engineering logs                                 |
| `infra/`             | Local stack (`docker-compose.yml`: Next.js, FastAPI, PostgreSQL, Redis, Celery) |
| `.env.example`       | Template for environment variables (placeholders only)         |

The frontend and backend can run independently for development. Docker Compose starts the
integrated application stack.

## Prerequisites

| Tool    | Version      | Notes                                                        |
| ------- | ------------ | ------------------------------------------------------------ |
| Node.js | 24 LTS       | `frontend/.nvmrc` pins 24.22.0                               |
| npm     | bundled      | ships with Node                                              |
| uv      | tested 0.12.3 | installs Python 3.13 automatically (`backend/.python-version`) |
| Docker  | with Compose v2 | runs the integrated local stack                              |

## Quick start

```bash
# Integrated production-like stack: http://localhost:3000, API http://localhost:8000
docker compose -f infra/docker-compose.yml up --build
```

For hot reload, start just PostgreSQL and Redis with Compose, then follow the development
instructions in [backend/README.md](backend/README.md) and [frontend/README.md](frontend/README.md).

## Checks

| Check        | Backend (`cd backend`)         | Frontend (`cd frontend`) |
| ------------ | ------------------------------ | ------------------------ |
| Lint         | `uv run ruff check .`          | `npm run lint`           |
| Format       | `uv run ruff format .`         | `npm run format`         |
| Format check | `uv run ruff format --check .` | `npm run format:check`   |
| Type check   | `uv run mypy`                  | `npm run typecheck`      |
| Tests        | `uv run pytest`                | `npm test`               |
| Build        | —                              | `npm run build`          |

Unit tests use deterministic fixtures. Database and worker integration tests require PostgreSQL
and Redis. The Playwright workflow uses a dedicated `*_e2e` database and a real Celery worker.
See [Milestone 11 validation notes](docs/engineering/11-validation-and-deployment.md).

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

The dashboard API lives under `/api` (overview, suites, experiments, runs with traces and
diagnosis, comparisons, review queue, calibration, and review submission/correction); the
schema is committed as `backend/schemas/openapi.json` and interactive docs are at `/docs` when
the backend runs. The local stack has no authentication and is for development only; put a
trusted authenticated reverse proxy in front before exposing it to users.

`GET /health` returns:

```json
{ "status": "ok", "service": "ai-reliability-lab-backend", "version": "0.1.0", "environment": "development" }
```

More detail: [backend/README.md](backend/README.md), [frontend/README.md](frontend/README.md),
[docs/engineering/](docs/engineering/).
