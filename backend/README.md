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
| Schema drift check | `uv run python -m app.domain.schema --check` |
| Migration drift check | `uv run alembic check` (needs `APP_DATABASE_URL`) |

## Configuration

Settings are read from environment variables prefixed with `APP_` (and from `backend/.env`
if present). See `src/app/config.py` and the root `.env.example`.

| Variable           | Default                       | Allowed values                        |
| ------------------ | ----------------------------- | ------------------------------------- |
| `APP_SERVICE_NAME` | `ai-reliability-lab-backend`  | any string                            |
| `APP_ENVIRONMENT`  | `development`                 | `development`, `test`, `production`   |
| `APP_DATABASE_URL` | unset (required for persistence) | SQLAlchemy URL (`postgresql+psycopg://...`) |
| `APP_TRACE_RETENTION_DAYS` | `30`                  | integer ≥ 1                            |
| `APP_TEST_DATABASE_URL` | falls back to `APP_DATABASE_URL` | server used for throwaway test databases |
| `APP_REQUIRE_POSTGRES` | unset                     | `1` makes unreachable-database tests fail |
| `APP_JUDGE_PROVIDER` | `none`                      | `none`, `groq` (optional LLM judge)    |
| `APP_GROQ_API_KEY` (or `GROQ_API_KEY`) | unset   | Groq API key; required when the provider is `groq` |
| `APP_GROQ_MODEL`   | `llama-3.3-70b-versatile`     | any Groq chat model that supports JSON mode |
| `APP_GROQ_BASE_URL` | `https://api.groq.com/openai/v1` | OpenAI-compatible base URL      |
| `APP_JUDGE_TIMEOUT_SECONDS` | `30`                 | > 0                                    |
| `APP_JUDGE_SAMPLES` | `1`                          | 1–5 independent judge calls per rubric criterion |

## Domain models and benchmark datasets

`src/app/domain/` holds the validated Pydantic models for benchmark suites, cases, scenarios,
faults, agents, experiments, runs, traces, evaluations, failure classifications, and human
reviews. They have no persistence or execution logic. See
[docs/engineering/02-domain-models.md](../docs/engineering/02-domain-models.md).

Bundled datasets live at `src/app/benchmarks/datasets/<suite-id>/<version>.json` and are
loaded with `app.benchmarks.loader.load_bundled_suite(suite_id, version)`. The sample suite
`research-agent-sample` 1.0.0 is **synthetic**: every place and fact in it is fictional.

The JSON Schema contract for dataset files is generated from the models into `schemas/`:

```bash
uv run python -m app.domain.schema          # regenerate after changing models
uv run python -m app.domain.schema --check  # exit 1 if committed schemas are stale
```

## Agents and mock tools

`src/app/agents/interface.py` defines the provider-independent `Agent` protocol. Agents call
tools only through `ToolExecutor` (`src/app/tools/executor.py`), which validates arguments and
outputs, returns structured `ToolError`s, and records every call. The harness function
`run_agent()` attaches the recorded execution log to the agent's answer. See
[docs/engineering/03-agent-interface.md](../docs/engineering/03-agent-interface.md).

`DeterministicResearchAgent` (no LLM) runs against mock `search_documents` / `fetch_document`
tools over a **synthetic** corpus. Example executions:

```bash
uv run python -m app.agents.demo                         # all fixtures, summarised
uv run python -m app.agents.demo tool-timeout            # one fixture
uv run python -m app.agents.demo --json missing-document # full AgentResult JSON
```

## Scenario runner and fault injection

`app.runner.ScenarioRunner` runs one benchmark case + scenario against an agent version under
explicit `ExecutionLimits` (max steps, max tool calls, deadline) and a seed, and returns a
`ScenarioRunResult` (run record, effective configuration, termination reason, execution log,
and the agent output for completed runs only). Scenario faults (`tool_timeout`, `rate_limit`,
`tool_error`, `malformed_output`, `empty_result`, `incomplete_result`, `incorrect_output`,
`prompt_injection`, `latency`) are applied by a `FaultInjector` between the executor and the
unmodified mock tools. See [docs/engineering/04-scenario-runner.md](../docs/engineering/04-scenario-runner.md).

```bash
uv run python -m app.runner.demo                          # synthetic case under every scenario
uv run python -m app.runner.demo --seed 3 search-flaky    # seeded probabilistic fault
uv run python -m app.runner.demo --json fetch-misleading  # full ScenarioRunResult JSON
```

## Evaluation and failure diagnosis

`app.evaluation.Evaluator` scores a stored run trace against its scenario's expectation in three
layers (answer, tool behaviour, execution) and returns a `CaseEvaluation` with a
`passed`/`failed`/`inconclusive` verdict, score bounds, failure labels, per-criterion evidence
(trace event IDs), and explanations. `app.evaluation.diagnosis.diagnose` turns an evaluation and
its trace into observations and candidate root causes. Rubric criteria use an optional LLM judge
(Groq); without one they are reported as inconclusive. Tests never call the network. See
[docs/engineering/06-evaluation-and-diagnosis.md](../docs/engineering/06-evaluation-and-diagnosis.md).

```bash
uv run python -m app.evaluation.demo                        # verdicts for every sample scenario
uv run python -m app.evaluation.demo -v fetch-misleading    # criteria + observations
uv run python -m app.evaluation.demo --json search-empty    # CaseEvaluation + Diagnosis JSON
APP_JUDGE_PROVIDER=groq GROQ_API_KEY=... uv run python -m app.evaluation.demo --judge  # uses Groq
```

## Experiments, versioning, and regression detection

An experiment records an immutable configuration snapshot (suite version and content hash,
per-scenario fingerprints, agent version, agent implementation, limits, repetitions, seed,
evaluator version). `ExperimentService.run_experiment(experiment_id, runner, evaluator)` stores
each run with its trace and its evaluation. `app.experiments.comparison.compare` compares two
experiments case by case; `app.experiments.policy.evaluate_policy` applies a `RegressionPolicy`.
See [docs/engineering/07-experiments-and-regressions.md](../docs/engineering/07-experiments-and-regressions.md).

```bash
uv run python -m app.experiments.demo no-retry               # offline: baseline vs a variant
uv run python -m app.experiments.demo slow-backoff --repetitions 2 --policy policy.json
uv run python -m app.experiments.compare EXP_A EXP_B --policy policy.json   # stored experiments
```

Both exit 0 when the policy passes and 1 on a regression; `compare` exits 2 when the experiments
cannot be compared (unknown ID, no configuration snapshot, invalid policy file).

## Human review and evaluator calibration

Reviewers label runs (`pass`, `fail`, `ambiguous`, `insufficiently_specified`) with failure
categories, confidence, and evidence. Reviews are append-only: a changed mind is a new record
that supersedes the old one (the database rejects UPDATE/DELETE). `app.review.queue` selects
runs that are security-sensitive, disputed, uncertain, or randomly sampled for calibration;
`app.review.agreement` compares the evaluator with human labels (confusion matrix,
precision/recall/F1, Cohen's kappa, per-category and reviewer-pair agreement). See
[docs/engineering/08-human-review-and-calibration.md](../docs/engineering/08-human-review-and-calibration.md).

```bash
uv run python -m app.review.demo              # metrics on SYNTHETIC fixtures (not a calibration)
uv run python -m app.review.demo imbalanced   # kappa paradox example
APP_DATABASE_URL=... uv run python -m app.review.report EXPERIMENT_ID [--scope all]
```

## Dashboard API

`/api/...` endpoints (FastAPI routers in `src/app/api/`) serve the Next.js dashboard from
persisted data. There is **no authentication**: anyone who can reach the server can read data
and submit reviews. The OpenAPI schema is committed as `schemas/openapi.json` (the frontend
generates its types from it); regenerate with `uv run python -m app.api.openapi_export`
(`--check` fails if stale; also enforced by `tests/test_openapi.py`).

```bash
uv run python -m app.seed_demo                 # DEMO data: synthetic suite, real runs, labelled [demo]
uv run python scripts/prepare_e2e_db.py        # drop + recreate + seed reliabilitylab_e2e (e2e only)
```

## Database (PostgreSQL)

Persistence uses PostgreSQL with SQLAlchemy 2.x and Alembic. Start the local database and
apply migrations:

```bash
docker compose -f ../infra/docker-compose.yml up -d postgres
export APP_DATABASE_URL=postgresql+psycopg://reliabilitylab:reliabilitylab_local_dev@127.0.0.1:5433/reliabilitylab
uv run alembic upgrade head        # apply migrations
uv run alembic current             # show the applied revision
uv run alembic check               # fail if models and migrations have drifted
```

`alembic check` (autogenerate) does not compare CHECK constraints: when you add a value to an
enum stored in a column, write the migration by hand (see `0002`). `tests/db/test_migrations.py`
compares every enum CHECK constraint in the database with the models.

After changing `src/app/db/models.py`, create a migration with
`uv run alembic revision --autogenerate -m "<change>"`, **review it by hand**, and commit it.
`alembic.ini` deliberately contains no database URL; `migrations/env.py` reads
`APP_DATABASE_URL` (or `-x url=...`).

### Database tests

`tests/db/` runs against real PostgreSQL. Each test session creates a throwaway database
`reliabilitylab_test_<random>` on the server given by `APP_TEST_DATABASE_URL` (default:
`APP_DATABASE_URL`, then the local compose URL), migrates it, and drops it afterwards; each test
is rolled back. If the server is unreachable the database tests are **skipped with the reason**
shown by `uv run pytest -ra`; set `APP_REQUIRE_POSTGRES=1` to make that a failure instead.

## Layout

```
src/app/
  main.py               # create_app() factory and ASGI `app`
  config.py             # pydantic-settings Settings
  api/health.py         # GET /health
  domain/               # Pydantic domain models, enums, IDs, timestamps, schema export
  benchmarks/           # dataset loader + bundled datasets (package data)
  runtime/              # Clock abstraction, execution log recorder, execution limits
  tools/                # tool contract, ToolExecutor, mock search/fetch tools
  agents/               # Agent protocol, deterministic research agent, fixtures, demo CLI
  runner/               # scenario runner, fault injector, synthetic samples, demo CLI
  tracing/              # trace event schema, collector, redaction/size policy, reconstruction
  db/                   # SQLAlchemy models, sessions, repositories (suites, runs, reviews)
  evaluation/           # evaluation engine, checks, LLM judge (Groq), diagnosis, demo CLI
  experiments/          # snapshots, executor, metrics, comparison, regression policy, CLIs
  review/               # human review records, queue, agreement metrics, fixtures, CLIs
  api/                  # FastAPI routers: health, dashboard read API, reviews, OpenAPI export
  seed_demo.py          # labelled demo data for local development and e2e tests
migrations/             # Alembic environment and revisions
schemas/                # generated JSON Schema files (committed)
tests/                  # pytest suite (no network, no external services)
```
