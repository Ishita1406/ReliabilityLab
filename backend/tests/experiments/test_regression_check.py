"""The CI regression-check command: pass, regression, and every way the check cannot run."""

import json
from pathlib import Path

import pytest

from app.experiments.regression_check import (
    EXIT_PASS,
    EXIT_REGRESSION,
    EXIT_UNCHECKED,
    Baseline,
    main,
)

BACKEND = Path(__file__).resolve().parents[2]
COMMITTED = BACKEND / "baselines" / "reliability-demo-1.0.0.json"
CI_POLICY = BACKEND / "baselines" / "ci-policy.json"
SUITE = "reliability-demo@1.0.0"


def check(baseline: Path, *extra: str, agent: str = "baseline") -> int:
    return main(
        [
            "--suite",
            SUITE,
            "--agent",
            agent,
            "--baseline",
            str(baseline),
            "--policy",
            str(CI_POLICY),
            *extra,
        ]
    )


def test_committed_baseline_is_valid_and_the_reference_agent_passes() -> None:
    baseline = Baseline.model_validate_json(COMMITTED.read_bytes())
    assert baseline.agent == "baseline" and baseline.repetitions == 2
    assert baseline.results.configuration is not None
    assert baseline.results.configuration.suite_id == "reliability-demo"
    assert len(baseline.results.runs) == 32
    assert check(COMMITTED, "--repetitions", "2") == EXIT_PASS


def test_seeded_regression_fails_the_check(capsys: pytest.CaptureFixture[str]) -> None:
    assert check(COMMITTED, "--repetitions", "2", agent="no-retry") == EXIT_REGRESSION
    out = capsys.readouterr().out
    assert "newly_failing: 5" in out
    assert "policy: FAILED" in out


def test_writing_a_baseline_then_checking_against_it(tmp_path: Path) -> None:
    target = tmp_path / "b.json"
    assert main(["--suite", SUITE, "--baseline", str(target), "--write-baseline"]) == EXIT_PASS
    assert check(target) == EXIT_PASS
    # Refuses to overwrite silently.
    assert main(["--suite", SUITE, "--baseline", str(target), "--write-baseline"]) == EXIT_UNCHECKED
    assert (
        main(["--suite", SUITE, "--baseline", str(target), "--write-baseline", "--force"])
        == EXIT_PASS
    )


def test_missing_baseline_never_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert check(tmp_path / "absent.json") == EXIT_UNCHECKED
    assert "does not exist" in capsys.readouterr().err


@pytest.mark.parametrize(
    "content",
    [
        "",
        "not json",
        json.dumps({"format": "something-else"}),
        json.dumps({"format": "reliabilitylab.regression-baseline", "format_version": 1}),
    ],
)
def test_invalid_baseline_never_passes(tmp_path: Path, content: str) -> None:
    target = tmp_path / "bad.json"
    target.write_text(content)
    assert check(target) == EXIT_UNCHECKED


def test_tampered_baseline_never_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = json.loads(COMMITTED.read_text())
    data["results"]["configuration"]["agent_version"]["system_prompt"] = "edited"
    target = tmp_path / "tampered.json"
    target.write_text(json.dumps(data))
    assert check(target) == EXIT_UNCHECKED
    assert "agent_version_sha256 does not match" in capsys.readouterr().err


def test_baseline_without_evaluated_runs_never_passes(tmp_path: Path) -> None:
    data = json.loads(COMMITTED.read_text())
    data["results"]["runs"] = []
    target = tmp_path / "empty.json"
    target.write_text(json.dumps(data))
    assert check(target) == EXIT_UNCHECKED


def test_baseline_for_another_benchmark_never_passes(tmp_path: Path) -> None:
    other = tmp_path / "other.json"
    assert (
        main(
            ["--suite", "research-agent-sample@1.0.0", "--baseline", str(other), "--write-baseline"]
        )
        == EXIT_PASS
    )
    assert check(other) == EXIT_UNCHECKED


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--agent", "nobody"], "unknown agent"),
        (["--suite", "no-such-suite@9.9.9"], "not found or invalid"),
        (["--suite", "missing-version"], "must look like"),
    ],
)
def test_bad_arguments_never_pass(
    args: list[str], message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    base = {"--suite": SUITE, "--agent": "baseline"}
    for flag, value in zip(args[::2], args[1::2], strict=True):
        base[flag] = value
    argv = [x for pair in base.items() for x in pair] + ["--baseline", str(COMMITTED)]
    assert main(argv) == EXIT_UNCHECKED
    assert message in capsys.readouterr().err


def test_invalid_policy_never_passes(tmp_path: Path) -> None:
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"max_success_rate_drop": "lots"}))
    assert (
        main(["--suite", SUITE, "--baseline", str(COMMITTED), "--policy", str(policy)])
        == EXIT_UNCHECKED
    )
