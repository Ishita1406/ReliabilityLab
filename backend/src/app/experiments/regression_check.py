"""CI regression check: run a benchmark with an agent configuration and compare to a baseline.

    uv run python -m app.experiments.regression_check \\
        --suite reliability-demo@1.0.0 --agent baseline \\
        --baseline baselines/reliability-demo-1.0.0.json --policy baselines/ci-policy.json

    # create or refresh a baseline (explicit; refuses to overwrite without --force)
    uv run python -m app.experiments.regression_check --suite reliability-demo@1.0.0 \\
        --agent baseline --baseline baselines/reliability-demo-1.0.0.json --write-baseline

Exit codes: **0** policy passed, **1** regression (policy failed), **2** the check could not be
performed. A missing, unreadable, invalid, tampered, or wrong-benchmark baseline is exit 2,
never a pass. The candidate is executed in memory with the deterministic runner (no database or
queue needed), so the check is reproducible in CI.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from app.benchmarks.loader import DatasetError, load_bundled_suite
from app.domain.base import DomainModel
from app.domain.benchmark import BenchmarkSuite
from app.domain.timestamps import UtcDateTime, utc_now
from app.experiments.comparison import ComparisonError, compare
from app.experiments.policy import RegressionPolicy, evaluate_policy
from app.experiments.records import ExperimentResults
from app.experiments.report import render
from app.experiments.samples import (
    BASELINE_VERSION,
    VARIANT_VERSIONS,
    run_sample_experiment,
    sample_configuration,
)

EXIT_PASS, EXIT_REGRESSION, EXIT_UNCHECKED = 0, 1, 2
AGENTS = {"baseline": BASELINE_VERSION, **VARIANT_VERSIONS}


class Baseline(DomainModel):
    format: Literal["reliabilitylab.regression-baseline"] = "reliabilitylab.regression-baseline"
    format_version: Literal[1] = 1
    created_at: UtcDateTime = Field(default_factory=utc_now)
    agent: str
    repetitions: int
    seed: int
    results: ExperimentResults


class CheckError(Exception):
    """The check cannot be performed (exit 2)."""


def _suite(spec: str) -> BenchmarkSuite:
    suite_id, _, version = spec.partition("@")
    if not suite_id or not version:
        raise CheckError(f"--suite must look like <id>@<version>, got '{spec}'")
    try:
        return load_bundled_suite(suite_id, version)
    except DatasetError as exc:
        raise CheckError(f"benchmark {spec} not found or invalid: {exc}") from exc


def run_candidate(suite_spec: str, agent: str, repetitions: int, seed: int) -> ExperimentResults:
    if agent not in AGENTS:
        raise CheckError(f"unknown agent '{agent}'; choose one of {', '.join(sorted(AGENTS))}")
    suite = _suite(suite_spec)
    configuration = sample_configuration(
        AGENTS[agent], suite=suite, repetitions=repetitions, seed=seed
    )
    return run_sample_experiment(f"regression-check-{agent}", configuration, suite=suite)


def load_baseline(path: Path, suite_spec: str) -> Baseline:
    if not path.exists():
        raise CheckError(f"baseline {path} does not exist (create one with --write-baseline)")
    try:
        baseline = Baseline.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise CheckError(f"baseline {path} is not a valid baseline file: {exc}") from exc
    configuration = baseline.results.configuration
    if configuration is None:
        raise CheckError(f"baseline {path} has no configuration snapshot")
    suite_id = suite_spec.partition("@")[0]
    if configuration.suite_id != suite_id:
        raise CheckError(
            f"baseline {path} is for benchmark '{configuration.suite_id}', not '{suite_id}'"
        )
    evaluated = [r for r in baseline.results.runs if r.evaluation is not None]
    if not evaluated:
        raise CheckError(f"baseline {path} contains no evaluated runs")
    return baseline


def load_policy(path: Path | None) -> RegressionPolicy:
    if path is None:
        return RegressionPolicy()
    try:
        return RegressionPolicy.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise CheckError(f"policy {path} is not a valid regression policy: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark regression check for CI.")
    parser.add_argument("--suite", required=True, help="<suite id>@<version> (bundled dataset)")
    parser.add_argument("--agent", default="baseline", help=f"one of {', '.join(sorted(AGENTS))}")
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--force", action="store_true", help="overwrite an existing baseline")
    parser.add_argument("--report", type=Path, help="also write the comparison as JSON here")
    args = parser.parse_args(argv)

    try:
        if args.write_baseline:
            if args.baseline.exists() and not args.force:
                raise CheckError(f"{args.baseline} exists; pass --force to overwrite it")
            results = run_candidate(args.suite, args.agent, args.repetitions, args.seed)
            baseline = Baseline(
                agent=args.agent, repetitions=args.repetitions, seed=args.seed, results=results
            )
            args.baseline.parent.mkdir(parents=True, exist_ok=True)
            args.baseline.write_text(baseline.model_dump_json(indent=1) + "\n", encoding="utf-8")
            passed = sum(1 for r in results.runs if r.evaluation and r.evaluation.passed)
            print(f"wrote {args.baseline}: {passed}/{len(results.runs)} runs passed")
            return EXIT_PASS
        _suite(args.suite)  # report a bad --suite before judging the baseline against it
        policy = load_policy(args.policy)
        baseline = load_baseline(args.baseline, args.suite)
        candidate = run_candidate(args.suite, args.agent, args.repetitions, args.seed)
        comparison = compare(baseline.results, candidate)
    except (CheckError, ComparisonError) as exc:
        print(f"regression check could not run: {exc}", file=sys.stderr)
        return EXIT_UNCHECKED

    evaluation = evaluate_policy(comparison, policy)
    print(f"baseline: {args.baseline} (agent {baseline.agent}, {baseline.created_at:%Y-%m-%d})")
    print(
        f"candidate: agent {args.agent} on {args.suite}, "
        f"{args.repetitions} repetition(s), seed {args.seed}"
    )
    print(render(comparison, evaluation))
    if args.report:
        args.report.write_text(
            json.dumps(
                {
                    "checked_at": datetime.now().astimezone().isoformat(),
                    "comparison": comparison.model_dump(mode="json"),
                    "policy": evaluation.model_dump(mode="json"),
                },
                indent=2,
            )
        )
    return EXIT_PASS if evaluation.passed else EXIT_REGRESSION


if __name__ == "__main__":
    raise SystemExit(main())
