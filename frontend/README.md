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

```bash
npm run dev      # http://localhost:3000
```

## Quality checks

| Purpose      | Command                |
| ------------ | ---------------------- |
| Lint         | `npm run lint`         |
| Format       | `npm run format`       |
| Format check | `npm run format:check` |
| Type check   | `npm run typecheck`    |
| Tests        | `npm test`             |
| Prod build   | `npm run build`        |

`typecheck` runs `next typegen` first so that Next's generated route types (e.g. the global
`LayoutProps`) exist before `tsc --noEmit`.

Tests use Vitest with jsdom and Testing Library; they live next to the code as `*.test.tsx`.

## Environment variables

None are required yet. Put local overrides in `frontend/.env.local` (git-ignored).
