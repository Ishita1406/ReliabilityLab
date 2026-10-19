"""The fixture demo labels itself as synthetic."""

import pytest

from app.review.demo import main


def test_demo_is_marked_not_empirical(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "NOT EMPIRICAL" in out
    assert "TP 20  FP 5  FN 10  TN 15" in out


def test_imbalanced_demo_shows_the_kappa_paradox(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["imbalanced"]) == 0
    assert "Class imbalance" in capsys.readouterr().out
