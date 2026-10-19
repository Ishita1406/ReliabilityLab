"""Run the synthetic sample case under every sample scenario.

uv run python -m app.runner.demo                          # matrix of all scenarios
uv run python -m app.runner.demo --seed 3 search-flaky    # one scenario, another seed
uv run python -m app.runner.demo --json fetch-misleading  # full ScenarioRunResult JSON
"""

import argparse

from app.runner.runner import ScenarioRunner, ScenarioRunResult
from app.runner.samples import EXPECTED, SCENARIOS, deterministic_research_agent, sample_request


def summarise(name: str, result: ScenarioRunResult) -> str:
    output = result.output
    outcome = output.answer.declared_outcome if output else "-"
    expected = EXPECTED.get(name)
    expectation = f"(expected {expected.status}/{expected.outcome})" if expected else "(seeded)"
    tool_by_call = {call.id: call.tool_name for call in result.execution.tool_calls}
    activations = ", ".join(
        f"{a.fault_id}->{tool_by_call[a.tool_call_id]}" for a in result.execution.fault_activations
    )
    calls = len(result.execution.tool_calls)
    elapsed = (result.execution.finished_at - result.execution.started_at).total_seconds()
    lines = [
        f"{name:24} {result.run.status:15} {outcome!s:25} {expectation}",
        f"    faults fired: {activations or '-'} | tool calls: {calls} "
        f"| virtual time: {elapsed:.3f} s",
    ]
    if output is not None:
        lines.append(f"    answer: {output.answer.content}")
    else:
        lines.append(f"    error: {result.run.error.message if result.run.error else '-'}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run sample scenarios through the runner.")
    parser.add_argument("scenarios", nargs="*", help=f"Any of: {', '.join(SCENARIOS)}.")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--json", action="store_true", help="Print full ScenarioRunResult JSON.")
    args = parser.parse_args(argv)
    unknown = [name for name in args.scenarios if name not in SCENARIOS]
    if unknown:
        parser.error(f"unknown scenarios: {', '.join(unknown)}")

    runner = ScenarioRunner(deterministic_research_agent)
    for name in args.scenarios or list(SCENARIOS):
        result = runner.run(sample_request(name, seed=args.seed))
        print(result.model_dump_json(indent=2) if args.json else summarise(name, result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
