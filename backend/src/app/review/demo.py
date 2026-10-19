"""Calibration report on the SYNTHETIC fixtures (implementation check, not a calibration).

uv run python -m app.review.demo              # main fixture
uv run python -m app.review.demo imbalanced   # kappa paradox fixture
"""

import argparse

from app.review.agreement import calibration_report
from app.review.fixtures import EVALUATOR_VERSION, calibration_fixture, imbalanced_fixture
from app.review.report import render


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "fixture", nargs="?", choices=["calibration", "imbalanced"], default="calibration"
    )
    args = parser.parse_args(argv)
    evaluations, reviews = (
        calibration_fixture() if args.fixture == "calibration" else imbalanced_fixture()
    )
    report = calibration_report(evaluations, reviews, evaluator_version=EVALUATOR_VERSION)
    print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
