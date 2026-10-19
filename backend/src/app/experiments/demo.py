"""Compare two agent configurations on the synthetic evaluation suite (in memory, offline).

uv run python -m app.experiments.demo                          # baseline vs no-retry
uv run python -m app.experiments.demo single-source --repetitions 2
uv run python -m app.experiments.demo prompt-b --policy policy.json --json

The exit code is the policy result: 0 passed, 1 regression detected.
"""

import argparse
import json
from pathlib import Path

from app.experiments.comparison import compare
from app.experiments.policy import RegressionPolicy, evaluate_policy
from app.experiments.report import render
from app.experiments.samples import (
    BASELINE_VERSION,
    VARIANT_VERSIONS,
    run_sample_experiment,
    sample_configuration,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "candidate", nargs="?", default="no-retry", choices=sorted(VARIANT_VERSIONS)
    )
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--policy", type=Path, help="RegressionPolicy JSON (default policy if omitted)"
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    policy = (
        RegressionPolicy.model_validate_json(args.policy.read_text(encoding="utf-8"))
        if args.policy
        else RegressionPolicy()
    )
    baseline = run_sample_experiment(
        "baseline",
        sample_configuration(BASELINE_VERSION, repetitions=args.repetitions, seed=args.seed),
    )
    candidate = run_sample_experiment(
        args.candidate,
        sample_configuration(
            VARIANT_VERSIONS[args.candidate], repetitions=args.repetitions, seed=args.seed
        ),
    )
    comparison = compare(baseline, candidate)
    evaluation = evaluate_policy(comparison, policy)
    if args.json:
        print(
            json.dumps(
                {
                    "comparison": comparison.model_dump(mode="json"),
                    "policy": evaluation.model_dump(mode="json"),
                },
                indent=2,
            )
        )
    else:
        print(render(comparison, evaluation))
    return evaluation.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
