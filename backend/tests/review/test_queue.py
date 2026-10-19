"""Review queue selection, sampling, and required reviews."""

import pytest

from app.experiments.records import ExperimentResults, RunRecord
from app.experiments.samples import (
    BASELINE_VERSION,
    EVALUATION_SUITE,
    run_sample_experiment,
    sample_configuration,
)
from app.review.models import HumanReview, ReviewLabel
from app.review.queue import (
    QueueItem,
    QueuePolicy,
    QueueReason,
    SamplingPolicy,
    build_queue,
    is_sampled,
    security_scenarios,
)
from tests.experiments.helpers import SCENARIOS, P, U, results

PREFIX = "runner.lighthouse-year."
NO_SAMPLING = QueuePolicy(sampling=SamplingPolicy(rate=1e-9))


@pytest.fixture(scope="module")
def sample() -> ExperimentResults:
    return run_sample_experiment("queue", sample_configuration(BASELINE_VERSION))


def record(results: ExperimentResults, name: str) -> RunRecord:
    return next(r for r in results.runs if r.scenario_id == PREFIX + name)


def human(
    rec: RunRecord, reviewer: str, label: ReviewLabel, rationale: str | None = None
) -> HumanReview:
    assert rec.evaluation is not None
    return HumanReview(
        run_id=rec.run_id,
        case_id=rec.case_id,
        scenario_id=rec.scenario_id,
        evaluator_version=rec.evaluation.evaluator_version,
        reviewer=reviewer,
        label=label,
        blind=True,
        provenance="synthetic",
        rationale=rationale,
    )


def by_scenario(items: list[QueueItem]) -> dict[str, QueueItem]:
    return {i.scenario_id.removeprefix(PREFIX): i for i in items}


def test_adversarial_scenarios_are_security_sensitive(sample: ExperimentResults) -> None:
    adversarial = security_scenarios(EVALUATION_SUITE)
    assert adversarial == {PREFIX + "fetch-prompt-injection"}
    items = by_scenario(build_queue(sample, [], NO_SAMPLING, adversarial_scenarios=adversarial))
    # The deterministic agent passes the injection scenario, but it is reviewed anyway: a
    # missed security failure is the costliest error.
    assert set(items) == {"fetch-prompt-injection"}
    item = items["fetch-prompt-injection"]
    assert item.reasons == [QueueReason.SECURITY_SENSITIVE]
    assert (item.required_reviews, item.open, item.blind) == (2, True, False)


def test_human_vs_evaluator_disagreement_is_disputed(sample: ExperimentResults) -> None:
    baseline = record(sample, "baseline")  # evaluator: passed
    reviews = [human(baseline, "alice", ReviewLabel.FAIL), human(baseline, "bob", ReviewLabel.FAIL)]
    item = by_scenario(build_queue(sample, reviews, NO_SAMPLING))["baseline"]
    assert item.reasons == [QueueReason.DISPUTED]
    assert item.current_reviewers == ["alice", "bob"]
    assert (item.required_reviews, item.open) == (3, True)  # needs an adjudicator


def test_reviewer_disagreement_is_disputed_and_agreement_is_not(sample: ExperimentResults) -> None:
    misleading = record(sample, "fetch-misleading")  # evaluator: failed
    split = [human(misleading, "a", ReviewLabel.FAIL), human(misleading, "b", ReviewLabel.PASS)]
    assert "fetch-misleading" in by_scenario(build_queue(sample, split, NO_SAMPLING))
    agree = [human(misleading, "a", ReviewLabel.FAIL), human(misleading, "b", ReviewLabel.FAIL)]
    assert "fetch-misleading" not in by_scenario(build_queue(sample, agree, NO_SAMPLING))
    ambiguous = [human(misleading, "a", ReviewLabel.AMBIGUOUS, rationale="unclear")]
    assert "fetch-misleading" not in by_scenario(build_queue(sample, ambiguous, NO_SAMPLING))


def test_inconclusive_evaluations_are_uncertain() -> None:
    synthetic = results("uncertain", {SCENARIOS[0]: [U], SCENARIOS[1]: [P]})
    [item] = build_queue(synthetic, [], NO_SAMPLING)
    assert item.scenario_id == SCENARIOS[0]
    assert (item.reasons, item.required_reviews) == ([QueueReason.UNCERTAIN], 1)


def test_sampling_is_deterministic_order_independent_and_close_to_the_rate() -> None:
    policy = SamplingPolicy(rate=0.2, seed=7)
    ids = [f"run_{i:032x}" for i in range(2000)]
    picked = [i for i in ids if is_sampled(i, policy)]
    assert picked == [i for i in reversed(ids) if is_sampled(i, policy)][::-1]
    assert 0.17 < len(picked) / len(ids) < 0.23  # binomial sd is ~0.009 at n=2000
    other_seed = [i for i in ids if is_sampled(i, SamplingPolicy(rate=0.2, seed=8))]
    assert picked != other_seed
    assert all(is_sampled(i, SamplingPolicy(rate=1.0)) for i in ids[:50])


def test_sampled_items_are_blind_and_need_two_reviews(sample: ExperimentResults) -> None:
    everything = QueuePolicy(sampling=SamplingPolicy(rate=1.0))
    items = build_queue(sample, [], everything)
    assert len(items) == len(sample.runs)
    plain = by_scenario(items)["baseline"]
    assert (plain.reasons, plain.blind, plain.required_reviews) == ([QueueReason.SAMPLED], True, 2)


def test_items_close_when_enough_reviewers_and_open_items_come_first(
    sample: ExperimentResults,
) -> None:
    everything = QueuePolicy(sampling=SamplingPolicy(rate=1.0))
    baseline = record(sample, "baseline")
    reviews = [human(baseline, "a", ReviewLabel.PASS), human(baseline, "b", ReviewLabel.PASS)]
    items = build_queue(sample, reviews, everything)
    closed = by_scenario(items)["baseline"]
    assert not closed.open
    assert items[-1] == closed
    assert all(i.open for i in items[:-1])
    priorities = [i.priority for i in items[:-1]]
    assert priorities == sorted(priorities)


def test_policy_must_cover_every_reason() -> None:
    with pytest.raises(ValueError, match="every queue reason"):
        QueuePolicy(reviews_required={QueueReason.SAMPLED: 1})
