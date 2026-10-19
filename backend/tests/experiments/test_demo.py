"""The offline comparison demo's exit codes follow the policy."""

import json
from pathlib import Path

import pytest

from app.experiments.demo import main


def test_regression_exits_1(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["no-retry"]) == 1
    out = capsys.readouterr().out
    assert "newly_failing: 2" in out and "policy: FAILED" in out


def test_prompt_change_exits_0(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["prompt-b"]) == 0
    assert "configuration differences:\n  agent_version" in capsys.readouterr().out


def test_policy_file_and_json_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"max_success_rate_drop": 0.5, "max_newly_failing_cases": 2}))
    assert main(["no-retry", "--policy", str(policy), "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["policy"]["passed"] is True
    assert len(document["comparison"]["units"]) == 12
