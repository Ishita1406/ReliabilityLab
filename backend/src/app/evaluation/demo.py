"""Run, evaluate, and diagnose the synthetic sample scenarios.

uv run python -m app.evaluation.demo                       # summary of every scenario
uv run python -m app.evaluation.demo fetch-misleading -v   # with criteria and observations
uv run python -m app.evaluation.demo --json search-empty   # CaseEvaluation + Diagnosis JSON
uv run python -m app.evaluation.demo --judge               # use the configured LLM judge

``--judge`` reads ``APP_JUDGE_PROVIDER`` / ``APP_GROQ_API_KEY`` and calls the provider; it is
the only mode that uses the network. The sample expectations have no rubric criteria, so the
judge is only used by the diagnosis advisor there.
"""

import argparse
import json

from app.config import get_settings
from app.evaluation.diagnosis import Diagnosis, DiagnosisAdvisor, diagnose
from app.evaluation.engine import Evaluator
from app.evaluation.groq import judge_client_from_settings
from app.evaluation.judge import JudgeSettings, LLMJudge
from app.evaluation.models import CaseEvaluation
from app.evaluation.samples import EVALUATION_CASE, EVALUATION_SCENARIOS
from app.runner.runner import ScenarioRunner
from app.runner.samples import deterministic_research_agent, sample_request
from app.tracing.collector import TraceCollector


def summarise(name: str, evaluation: CaseEvaluation, diagnosis: Diagnosis, verbose: bool) -> str:
    labels = ", ".join(evaluation.labels) or "-"
    recovered = sum(1 for f in evaluation.tool_failures if f.recovered)
    lines = [
        f"{name:24} {evaluation.verdict:12} score {evaluation.score:.2f} "
        f"labels: {labels} | tool failures: {len(evaluation.tool_failures)} "
        f"({recovered} recovered)",
        "    layers: " + ", ".join(f"{layer.layer}={layer.verdict}" for layer in evaluation.layers),
    ]
    for candidate in diagnosis.candidates:
        lines.append(
            f"    candidate [{candidate.strength}/{candidate.basis}] {candidate.cause}: "
            f"{candidate.explanation}"
        )
    if verbose:
        for criterion in evaluation.criteria:
            lines.append(
                f"      - {criterion.criterion_id}: {criterion.status}: {criterion.explanation}"
            )
        for observation in diagnosis.observations:
            lines.append(f"      * {observation.id} [{observation.kind}] {observation.statement}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate and diagnose the sample scenarios.")
    parser.add_argument("scenarios", nargs="*", help=f"Any of: {', '.join(EVALUATION_SCENARIOS)}.")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--judge", action="store_true", help="Use the configured LLM judge.")
    args = parser.parse_args(argv)
    unknown = [name for name in args.scenarios if name not in EVALUATION_SCENARIOS]
    if unknown:
        parser.error(f"unknown scenarios: {', '.join(unknown)}")

    client = judge_client_from_settings(get_settings()) if args.judge else None
    if args.judge and client is None:
        parser.error("--judge needs APP_JUDGE_PROVIDER=groq and an API key")
    judge = (
        LLMJudge(client, JudgeSettings(samples=get_settings().judge_samples)) if client else None
    )
    evaluator = Evaluator(judge)
    advisor = DiagnosisAdvisor(client) if client else None
    runner, collector = ScenarioRunner(deterministic_research_agent), TraceCollector()

    for name in args.scenarios or list(EVALUATION_SCENARIOS):
        scenario = EVALUATION_SCENARIOS[name]
        request = sample_request(name, seed=args.seed).updated(
            case=EVALUATION_CASE.model_dump(), scenario=scenario.model_dump()
        )
        trace = collector.collect(runner.run(request))
        evaluation = evaluator.evaluate(trace, EVALUATION_CASE, scenario)
        diagnosis = diagnose(evaluation, trace)
        if advisor is not None:
            diagnosis = advisor.augment(diagnosis)
        if args.json:
            print(
                json.dumps(
                    {
                        "evaluation": evaluation.model_dump(mode="json"),
                        "diagnosis": diagnosis.model_dump(mode="json"),
                    },
                    indent=2,
                )
            )
        else:
            print(summarise(name, evaluation, diagnosis, args.verbose))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
