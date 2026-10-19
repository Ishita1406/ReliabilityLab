"""Render a calibration report as text; CLI for stored experiments.

APP_DATABASE_URL=... uv run python -m app.review.report EXPERIMENT_ID [--scope all] [--json]
"""

import argparse

from app.db.session import create_db_engine, database_url, session_factory
from app.review.agreement import BinaryAgreement, CalibrationReport, Estimate
from app.review.service import ReviewService


def fmt(estimate: Estimate, percent: bool = True) -> str:
    if estimate.value is None:
        return f"undefined ({estimate.unavailable_reason})"
    value = f"{estimate.value:.1%}" if percent else f"{estimate.value:.3f}"
    if estimate.ci_low is not None and estimate.ci_high is not None:
        low, high = (
            (f"{estimate.ci_low:.1%}", f"{estimate.ci_high:.1%}")
            if percent
            else (f"{estimate.ci_low:.3f}", f"{estimate.ci_high:.3f}")
        )
        return f"{value} [{low}, {high}] (n={estimate.n})"
    return f"{value} (n={estimate.n}{'; ' + estimate.note if estimate.note else ''})"


def _binary(lines: list[str], indent: str, agreement: BinaryAgreement) -> None:
    m = agreement.matrix
    lines.append(f"{indent}confusion (positive = fail): TP {m.tp}  FP {m.fp}  FN {m.fn}  TN {m.tn}")
    lines.append(f"{indent}agreement {fmt(agreement.agreement)}")
    lines.append(f"{indent}precision {fmt(agreement.precision)}  recall {fmt(agreement.recall)}")
    lines.append(f"{indent}F1 {fmt(agreement.f1, percent=False)}")
    lines.append(
        f"{indent}kappa {fmt(agreement.kappa, percent=False)}  "
        f"PABAK {fmt(agreement.pabak, percent=False)}"
    )
    lines.append(f"{indent}reference failure rate {fmt(agreement.reference_positive_rate)}")


def render(report: CalibrationReport) -> str:
    s = report.summary
    lines = [
        f"evaluator {report.evaluator_version} vs human reviews (scope: {report.scope})",
        "EMPIRICAL (real human labels)" if report.empirical else "NOT EMPIRICAL (see caveats)",
        f"reviewed runs {s.reviewed_runs} | reviews {s.reviews} ({s.corrections} corrections) | "
        f"reviewers {', '.join(s.reviewers) or '-'}",
        f"reference: definite {s.definite}, disputed {s.disputed}, ambiguous {s.ambiguous}, "
        f"insufficiently specified {s.insufficiently_specified}",
        f"compared {s.compared} | evaluator abstained {s.automated_inconclusive} | "
        f"excluded (not blind) {s.excluded_not_blind} | missing evaluation {s.missing_evaluation}",
        "automated vs human:",
    ]
    _binary(lines, "  ", report.automated_vs_human)
    for category in report.categories:
        lines.append(
            f"category {category.category} (human-assessed runs {category.human_assessed_runs}):"
        )
        _binary(lines, "  ", category.agreement)
    for pair in report.reviewer_pairs:
        lines.append(
            f"reviewers {pair.reviewer_a} vs {pair.reviewer_b}: "
            f"both reviewed {pair.both_reviewed}, "
            f"both definite {pair.both_definite}, not definite {pair.one_or_both_not_definite}"
        )
        lines.append(f"  agreement {fmt(pair.agreement)}  kappa {fmt(pair.kappa, percent=False)}")
    lines.append("caveats:")
    lines += [f"  - {c}" for c in report.caveats] or ["  - none"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Calibration report for a stored experiment.")
    parser.add_argument("experiment_id")
    parser.add_argument("--scope", choices=["sampled", "all"], default="sampled")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    engine = create_db_engine(database_url())
    try:
        report = ReviewService(session_factory(engine)).report(args.experiment_id, scope=args.scope)
    finally:
        engine.dispose()
    print(report.model_dump_json(indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
