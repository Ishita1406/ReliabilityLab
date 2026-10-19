"""Dashboard and review API against real PostgreSQL with seeded demo experiments."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_sessions
from app.main import create_app
from app.seed_demo import seed

PREFIX = "runner.lighthouse-year."


@pytest.fixture
def seeded(sessions: sessionmaker[Session]) -> dict[str, str]:
    return seed(sessions)


@pytest.fixture
def client(sessions: sessionmaker[Session]) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_sessions] = lambda: sessions
    with TestClient(app) as test_client:
        yield test_client


def test_suites(client: TestClient, seeded: dict[str, str]) -> None:
    suites = {s["id"]: s for s in client.get("/api/suites").json()}
    assert set(suites) == {"evaluation-samples", "research-agent-sample"}
    suite = suites["evaluation-samples"]
    assert (suite["id"], suite["version"], suite["case_count"], suite["scenario_count"]) == (
        "evaluation-samples",
        "1.0.0",
        1,
        12,
    )
    detail = client.get("/api/suites/evaluation-samples/1.0.0").json()
    assert len(detail["scenarios"]) == 12
    assert client.get("/api/suites/evaluation-samples/9.9.9").status_code == 404


def test_experiment_listing_and_detail(client: TestClient, seeded: dict[str, str]) -> None:
    listing = {e["name"]: e for e in client.get("/api/experiments").json()}
    assert len(listing) == 4
    pending = listing["[demo] pending (0.1.0)"]
    assert (pending["status"], pending["stored_runs"], pending["planned_runs"]) == (
        "pending",
        0,
        12,
    )

    detail = client.get(f"/api/experiments/{seeded['baseline']}").json()
    assert detail["configuration"]["agent_version"]["version_label"] == "0.1.0"
    metrics = detail["metrics"]
    assert metrics["task_success_rate"]["numerator"] == 11
    assert metrics["task_success_rate"]["denominator"] == 12
    assert metrics["latency_p95"]["value"] is None  # 12 runs < 20: explicitly unavailable
    assert "p95 needs at least 20 runs" in metrics["latency_p95"]["unavailable_reason"]
    failed = [r for r in detail["runs"] if r["verdict"] == "failed"]
    assert [r["scenario_id"] for r in failed] == [PREFIX + "fetch-misleading"]

    empty = client.get(f"/api/experiments/{seeded['pending']}").json()
    assert empty["runs"] == []
    assert empty["metrics"]["task_success_rate"]["unavailable_reason"] == "no evaluated runs"
    assert client.get("/api/experiments/exp_" + "0" * 32).status_code == 404


def test_run_detail_has_trace_evaluation_and_diagnosis(
    client: TestClient, seeded: dict[str, str]
) -> None:
    runs = client.get(
        f"/api/experiments/{seeded['baseline']}/runs", params={"verdict": "failed"}
    ).json()
    [run] = runs
    detail = client.get(f"/api/runs/{run['run_id']}").json()
    assert detail["evaluation"]["verdict"] == "failed"
    events = detail["trace"]["events"]
    assert events[0]["kind"] == "run" and events[0]["parent_id"] is None
    assert any(e["kind"] == "fault_injected" for e in events)
    diagnosis = detail["diagnosis"]
    assert diagnosis["observations"] and diagnosis["candidates"]
    assert all(c["basis"] == "rule" for c in diagnosis["candidates"])
    assert detail["trace_unavailable_reason"] is None


def test_comparison(client: TestClient, seeded: dict[str, str]) -> None:
    body = client.get(
        "/api/comparisons", params={"baseline": seeded["baseline"], "candidate": seeded["no-retry"]}
    ).json()
    newly_failing = {
        u["scenario_id"].removeprefix(PREFIX)
        for u in body["comparison"]["units"]
        if u["status"] == "newly_failing"
    }
    assert newly_failing == {"search-timeout-once", "search-malformed-once"}
    assert body["evaluation"]["passed"] is False
    missing = client.get(
        "/api/comparisons", params={"baseline": seeded["baseline"], "candidate": "exp_" + "0" * 32}
    )
    assert missing.status_code == 404


def test_overview(client: TestClient, seeded: dict[str, str]) -> None:
    overview = client.get("/api/overview").json()
    assert overview["total_experiments"] == 4
    assert overview["experiment_counts"]["completed"] == 3
    assert overview["experiment_counts"]["pending"] == 1
    assert overview["latest_completed"]["experiment"]["status"] == "completed"
    assert len(overview["recent_runs"]) == 15


def test_review_submission_correction_and_history(
    client: TestClient, seeded: dict[str, str]
) -> None:
    run_id = client.get(f"/api/experiments/{seeded['baseline']}/runs").json()[0]["run_id"]
    created = client.post(
        f"/api/runs/{run_id}/reviews",
        json={
            "reviewer": "alice",
            "label": "fail",
            "blind": True,
            "categories": {"task_correctness": "present"},
        },
    )
    assert created.status_code == 201
    review = created.json()
    assert review["provenance"] == "human"

    second = client.post(
        f"/api/runs/{run_id}/reviews", json={"reviewer": "alice", "label": "pass", "blind": True}
    )
    assert second.status_code == 409  # must be a correction, not a second root

    corrected = client.post(
        f"/api/reviews/{review['id']}/corrections",
        json={"reason": "misread the answer", "label": "pass"},
    )
    assert corrected.status_code == 201
    stale = client.post(
        f"/api/reviews/{review['id']}/corrections", json={"reason": "again", "label": "fail"}
    )
    assert stale.status_code == 409

    history = client.get(f"/api/runs/{run_id}/reviews").json()
    assert [r["label"] for r in history["records"]] == ["fail", "pass"]
    assert [r["id"] for r in history["current"]] == [corrected.json()["id"]]

    invalid = client.post(
        f"/api/runs/{run_id}/reviews", json={"reviewer": "bob", "label": "ambiguous", "blind": True}
    )
    assert invalid.status_code == 422  # ambiguous needs a rationale


def test_queue_and_calibration(client: TestClient, seeded: dict[str, str]) -> None:
    queue = client.get(f"/api/experiments/{seeded['baseline']}/review-queue").json()
    by_scenario = {item["scenario_id"].removeprefix(PREFIX): item for item in queue}
    assert "security_sensitive" in by_scenario["fetch-prompt-injection"]["reasons"]
    report = client.get(
        f"/api/experiments/{seeded['baseline']}/calibration", params={"scope": "all"}
    ).json()
    assert report["summary"]["reviews"] == 0
    assert report["empirical"] is False
