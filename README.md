# AI Reliability Lab

A platform for testing the reliability of AI agents.

This repository currently contains the project foundation only (Milestone 1): a Next.js
frontend, a FastAPI backend with a health endpoint, and the tooling to lint, format,
type-check, and test both. There are no databases, queues, agent frameworks, or LLM
integrations yet.

## Repository layout

| Path                 | Contents                                                       |
| -------------------- | -------------------------------------------------------------- |
| `frontend/`          | Next.js 16 App Router app (TypeScript, Tailwind CSS 4)         |
| `backend/`           | FastAPI service (Python 3.13, managed with uv)                 |
| `docs/engineering/`  | Per-milestone engineering logs                                 |
| `infra/`             | Reserved for Docker and infrastructure configuration           |
| `.env.example`       | Template for environment variables (placeholders only)         |

Frontend and backend are independent projects: each has its own toolchain, lockfile, and
commands, and neither needs the other to run or be tested.

## Prerequisites

| Tool    | Version      | Notes                                                        |
| ------- | ------------ | ------------------------------------------------------------ |
| Node.js | 24 LTS       | `frontend/.nvmrc` pins 24.22.0                               |
| npm     | bundled      | ships with Node                                              |
| uv      | tested 0.12.3 | installs Python 3.13 automatically (`backend/.python-version`) |

## Quick start

```bash
# Backend: http://localhost:8000/health
cd backend
uv sync --locked
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

Automated tests do not use the network or any external service.

## Environment variables

See `.env.example`. Copy the backend section to `backend/.env` and the frontend section to
`frontend/.env.local`. All variables are currently optional; real `.env` files are
git-ignored.

| Variable           | App      | Default                      | Purpose                                     |
| ------------------ | -------- | ---------------------------- | ------------------------------------------- |
| `APP_SERVICE_NAME` | backend  | `ai-reliability-lab-backend` | Service name reported by `/health`          |
| `APP_ENVIRONMENT`  | backend  | `development`                | `development`, `test`, or `production`      |

## API

`GET /health` returns:

```json
{ "status": "ok", "service": "ai-reliability-lab-backend", "version": "0.1.0", "environment": "development" }
```

More detail: [backend/README.md](backend/README.md), [frontend/README.md](frontend/README.md),
[docs/engineering/](docs/engineering/).
