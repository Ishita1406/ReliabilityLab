"""The evaluation demo CLI runs offline by default."""

import json

import pytest

from app.evaluation.demo import main


def test_demo_summary(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["baseline", "search-timeout-once"]) == 0
    out = capsys.readouterr().out
    assert "baseline                 passed" in out
    assert "tool failures: 1 (1 recovered)" in out


def test_demo_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--json", "search-empty"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert set(document) == {"evaluation", "diagnosis"}
    assert document["evaluation"]["verdict"] == "passed"


def test_demo_judge_requires_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("APP_JUDGE_PROVIDER", "none")
    get_settings.cache_clear()
    try:
        with pytest.raises(SystemExit):
            main(["--judge", "baseline"])
    finally:
        get_settings.cache_clear()
