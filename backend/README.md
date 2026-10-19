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

## Configuration

Settings are read from environment variables prefixed with `APP_` (and from `backend/.env`
if present). See `src/app/config.py` and the root `.env.example`.

| Variable           | Default                       | Allowed values                        |
| ------------------ | ----------------------------- | ------------------------------------- |
| `APP_SERVICE_NAME` | `ai-reliability-lab-backend`  | any string                            |
| `APP_ENVIRONMENT`  | `development`                 | `development`, `test`, `production`   |

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
schemas/                # generated JSON Schema files (committed)
tests/                  # pytest suite (no network, no external services)
```
