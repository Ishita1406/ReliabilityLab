"""Compare two stored experiments and apply a regression policy.

    APP_DATABASE_URL=... uv run python -m app.experiments.compare BASELINE_ID CANDIDATE_ID \\
        [--policy policy.json] [--pricing prices.json] [--json]

Exit codes (meant for a future CI step): 0 = policy passed, 1 = regression detected,
2 = the experiments could not be compared (missing snapshot, unknown ID, bad policy file).
"""

import argparse
import json
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.db.benchmarks import NotFoundError
from app.db.evaluations import load_experiment_results
from app.db.session import create_db_engine, database_url, session_factory
from app.experiments.comparison import ComparisonError, compare
from app.experiments.policy import RegressionPolicy, evaluate_policy
from app.experiments.pricing import PricingTable
from app.experiments.report import render

EXIT_NOT_COMPARABLE = 2


def compare_stored(
    sessions: sessionmaker[Session],
    baseline_id: str,
    candidate_id: str,
    *,
    policy: RegressionPolicy,
    pricing: PricingTable | None = None,
    as_json: bool = False,
) -> tuple[int, str]:
    """``(exit_code, report)`` for two stored experiments."""
    try:
        with sessions() as session:
            baseline = load_experiment_results(session, baseline_id)
            candidate = load_experiment_results(session, candidate_id)
        comparison = compare(baseline, candidate, pricing=pricing)
    except (NotFoundError, ComparisonError) as exc:
        return EXIT_NOT_COMPARABLE, f"error: {exc}"
    evaluation = evaluate_policy(comparison, policy)
    if as_json:
        report = json.dumps(
            {
                "comparison": comparison.model_dump(mode="json"),
                "policy": evaluation.model_dump(mode="json"),
            },
            indent=2,
        )
    else:
        report = render(comparison, evaluation)
    return evaluation.exit_code, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare two stored experiments.")
    parser.add_argument("baseline")
    parser.add_argument("candidate")
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--pricing", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        policy = (
            RegressionPolicy.model_validate_json(args.policy.read_text(encoding="utf-8"))
            if args.policy
            else RegressionPolicy()
        )
        pricing = (
            PricingTable.model_validate_json(args.pricing.read_text(encoding="utf-8"))
            if args.pricing
            else None
        )
    except (OSError, ValidationError) as exc:
        print(f"error: invalid policy or pricing file: {exc}")
        return EXIT_NOT_COMPARABLE

    engine = create_db_engine(database_url())
    try:
        code, report = compare_stored(
            session_factory(engine),
            args.baseline,
            args.candidate,
            policy=policy,
            pricing=pricing,
            as_json=args.json,
        )
    finally:
        engine.dispose()
    print(report)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
