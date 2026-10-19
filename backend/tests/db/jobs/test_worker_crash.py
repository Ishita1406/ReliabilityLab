"""A real worker process is killed (SIGKILL) mid-experiment; another worker finishes the job.

Uses two `celery worker` subprocesses (prefork pool) against the real Redis and a test
database, and the test-only slow agent so the kill lands between runs. Recovery can come from
either Redis redelivering the unacknowledged message (``visibility_timeout``) or the second
worker's start-up recovery re-publishing the job whose lease expired; the assertions hold for
both, and the test reports which happened.
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from tests.db.jobs.conftest import Harness
from tests.db.jobs.slow_agent import SLOW_VERSION
from tests.db.jobs.test_background_execution import PLANNED, stored_runs

BACKEND = Path(__file__).resolve().parents[3]


def start_worker(harness: Harness, name: str, log: Path) -> subprocess.Popen[bytes]:
    env = {
        **os.environ,
        "PYTHONPATH": str(BACKEND),
        "APP_DATABASE_URL": harness.db.url,
        "APP_REDIS_URL": harness.default_redis_url,
        "APP_AGENT_PLUGINS": "tests.db.jobs.slow_agent",
        "APP_JOB_LEASE_SECONDS": "2",
        "APP_BROKER_VISIBILITY_TIMEOUT_SECONDS": "3",
        "APP_WORKER_CONCURRENCY": "1",
        "SLOW_AGENT_SECONDS": "0.3",
    }
    with log.open("wb") as handle:  # the child keeps its own copy of the descriptor
        return subprocess.Popen(
            [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "app.jobs.celery_app",
                "worker",
                "--pool=prefork",
                "--concurrency=1",
                "--loglevel=WARNING",
                "--without-gossip",
                "--without-mingle",
                "-n",
                f"{name}@%h",
            ],
            cwd=BACKEND,
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # own process group: SIGKILL reaches the pool child too
        )


def stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def test_killed_worker_is_replaced_without_duplicate_runs(harness: Harness, tmp_path: Path) -> None:
    experiment_id = str(harness.create(agent_version_id=SLOW_VERSION.id)["experiment_id"])
    first = start_worker(harness, "doomed", tmp_path / "doomed.log")
    try:
        harness.wait_for(
            experiment_id,
            lambda s: s["progress"]["stored_runs"] >= 3,  # type: ignore[index]
            timeout=60,
        )
        os.killpg(first.pid, signal.SIGKILL)  # no shutdown, no ack, lease left behind
        first.wait(timeout=10)
    finally:
        if first.poll() is None:
            os.killpg(first.pid, signal.SIGKILL)

    at_kill = harness.status(experiment_id)
    assert at_kill["status"] == "running"  # nothing marked it finished: the worker just died
    assert at_kill["attempts"] == 1
    kept = at_kill["progress"]["stored_runs"]  # type: ignore[index]
    assert 3 <= kept < PLANNED

    time.sleep(2.5)  # let the dead worker's 2-second lease expire
    second = start_worker(harness, "replacement", tmp_path / "replacement.log")
    try:
        final = harness.wait_terminal(experiment_id, timeout=90)
    finally:
        stop(second)

    assert final["status"] == "completed"
    assert final["attempts"] == 2  # one takeover, no extra attempts
    runs = stored_runs(harness, experiment_id)
    assert len(runs) == len(set(runs)) == PLANNED  # resumed, nothing duplicated

    log = (tmp_path / "replacement.log").read_text(errors="replace")
    events = [json.loads(line)["event"] for line in log.splitlines() if line.startswith("{")]
    skipped = events.count("run.skipped")
    assert skipped == kept  # the replacement skipped exactly the runs stored before the crash
    via = "start-up recovery" if "job.republished" in events else "Redis redelivery"
    print(f"recovered via {via}; skipped {skipped} stored runs")
