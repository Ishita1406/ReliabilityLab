# AI Reliability Lab — Frontend

Next.js 16 (App Router) + React 19 + TypeScript (strict) + Tailwind CSS 4.

## Requirements

- Node.js 24 LTS (`.nvmrc` pins 24.22.0; `nvm use` picks it up). `package.json` declares
  `engines.node` and `.npmrc` sets `engine-strict=true`, so installs on other majors fail fast.
- npm (bundled with Node). Dependencies are pinned exactly and locked in `package-lock.json`.

## Setup

```bash
cd frontend
nvm use          # or otherwise ensure Node 24 is on PATH
npm ci           # reproducible install from package-lock.json
```

## Development

The dashboard reads everything from the FastAPI backend; it has no data of its own.

```bash
# Backend (in backend/): API on :8000. Optionally seed clearly labelled demo data first.
uv run python -m app.seed_demo
uv run uvicorn app.main:app --reload --port 8000

# Frontend
npm run dev      # http://localhost:3000
```

Pages: Overview (`/`), Benchmarks (`/benchmarks`), Experiments (`/experiments`), trace explorer
(`/runs/<id>`), Compare (`/compare`), Review queue (`/review`), failure review
(`/runs/<id>/review`). Architecture: [docs/engineering/09-nextjs-dashboard.md](../docs/engineering/09-nextjs-dashboard.md).

**Security:** there is no authentication. Anyone who can reach the dashboard (or the API) can
read data and submit reviews. Run it locally only.

## Quality checks

| Purpose      | Command                                         |
| ------------ | ----------------------------------------------- |
| Lint         | `npm run lint`                                  |
| Format       | `npm run format`                                |
| Format check | `npm run format:check`                          |
| Type check   | `npm run typecheck`                             |
| Tests        | `npm test`                                      |
| E2E tests    | `npm run test:e2e`                              |
| Prod build   | `npm run build`                                 |
| API types    | `npm run api:types` / `npm run api:types:check` |

`typecheck` runs `next typegen` first so that Next's generated route types (e.g. the global
`LayoutProps`) exist before `tsc --noEmit`.

Tests use Vitest with jsdom and Testing Library; they live next to the code as `*.test.tsx`.
Component tests use real API responses captured from a seeded backend
(`src/test/fixtures/`, refreshed with `node scripts/capture-fixtures.mjs`).

**API types** are generated from the backend's OpenAPI schema (`backend/schemas/openapi.json`)
into `src/lib/api/schema.d.ts`. After changing the backend API, run
`uv run python -m app.api.openapi_export` in `backend/`, then `npm run api:types` here.

**End-to-end tests** (Playwright, Chromium) need the local PostgreSQL and `uv`. They drop,
recreate, and seed the dedicated database `reliabilitylab_e2e` (override with
`E2E_DATABASE_URL`; the name must end in `_e2e`), start the backend on :8010 and a production
build on :3010, and run `e2e/*.spec.ts`. First run: `npx playwright install chromium`.

## Environment variables

| Variable                 | Default                 | Purpose                             |
| ------------------------ | ----------------------- | ----------------------------------- |
| `RELIABILITYLAB_API_URL` | `http://127.0.0.1:8000` | Backend base URL (server-side only) |

Put local overrides in `frontend/.env.local` (git-ignored).
