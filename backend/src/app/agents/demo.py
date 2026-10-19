"""Run the deterministic research agent against the bundled fixtures.

    uv run python -m app.agents.demo                 # summary of every fixture
    uv run python -m app.agents.demo tool-timeout    # one fixture
    uv run python -m app.agents.demo --json successful-research   # full AgentResult JSON

Run IDs are derived from the fixture name, so output is identical on every execution.
"""

import argparse

from app.agents.fixtures import FIXTURES, ResearchFixture
from app.agents.interface import AgentResult, run_agent
from app.agents.research import DeterministicResearchAgent
from app.domain.ids import IdPrefix, derived_id
from app.runtime.execution import DecisionStep, RejectedToolRequestStep, ToolCallStep


def execute(fixture: ResearchFixture) -> AgentResult:
    environment, _, _ = fixture.build(derived_id(IdPrefix.RUN, "demo", fixture.name))
    return run_agent(DeterministicResearchAgent(), fixture.task, environment)


def summarise(fixture: ResearchFixture, result: AgentResult) -> str:
    lines = [
        f"=== {fixture.name}: {fixture.description}",
        f"outcome:   {result.answer.declared_outcome} (expected {fixture.expected_outcome})",
        f"evidence:  {result.metadata.evidence_status} | stop: {result.metadata.stop_reason}",
        f"answer:    {result.answer.content}",
        f"sources:   {', '.join(result.answer.source_ids) or '-'}",
        "steps:",
    ]
    for step in result.execution.steps:
        if isinstance(step, ToolCallStep):
            call = step.tool_call
            detail = call.error_message or "ok"
            lines.append(
                f"  tool  {call.tool_name}({call.arguments}) -> {call.status} "
                f"[{call.latency_ms:.0f} ms] {detail}"
            )
        elif isinstance(step, RejectedToolRequestStep):
            lines.append(f"  reject {step.requested_name}: {step.error.message}")
        elif isinstance(step, DecisionStep):
            lines.append(f"  note  {step.message}")
    elapsed = (result.execution.finished_at - result.execution.started_at).total_seconds()
    lines.append(f"virtual time elapsed: {elapsed:.3f} s")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run research-agent fixtures.")
    parser.add_argument("fixtures", nargs="*", help=f"Any of: {', '.join(FIXTURES)}.")
    parser.add_argument("--json", action="store_true", help="Print the full AgentResult JSON.")
    args = parser.parse_args(argv)
    unknown = [name for name in args.fixtures if name not in FIXTURES]
    if unknown:
        parser.error(f"unknown fixtures: {', '.join(unknown)}")

    for name in args.fixtures or list(FIXTURES):
        fixture = FIXTURES[name]
        result = execute(fixture)
        print(result.model_dump_json(indent=2) if args.json else summarise(fixture, result))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
