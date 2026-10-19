"""Case-level comparison semantics, missing data, and changed benchmark versions."""

import pytest

from app.experiments.comparison import ComparisonError, UnitOutcome, UnitStatus, compare
from app.experiments.samples import (
    BASELINE_VERSION,
    EVALUATION_SUITE,
    VARIANT_VERSIONS,
    sample_configuration,
)
from tests.experiments.helpers import SCENARIOS, F, P, U, results

A, B, C, D = SCENARIOS[:4]


def statuses(
    baseline: dict[str, list[str | None]], candidate: dict[str, list[str | None]]
) -> dict[str, UnitStatus]:
    comparison = compare(results("base", baseline), results("cand", candidate))
    return {u.scenario_id: u.status for u in comparison.units}


def test_transitions() -> None:
    assert statuses({A: [P], B: [F], C: [P], D: [F]}, {A: [P], B: [P], C: [F], D: [F]}) == {
        A: UnitStatus.UNCHANGED,
        B: UnitStatus.NEWLY_PASSING,
        C: UnitStatus.NEWLY_FAILING,
        D: UnitStatus.UNCHANGED,
    }


def test_a_unit_passes_only_if_every_repetition_passes() -> None:
    comparison = compare(results("b", {A: [P, P, P]}), results("c", {A: [P, F, P]}))
    [unit] = comparison.units
    assert unit.status is UnitStatus.NEWLY_FAILING
    assert unit.candidate is not None
    assert (unit.candidate.outcome, unit.candidate.passed, unit.candidate.failed) == (
        UnitOutcome.FAIL,
        2,
        1,
    )


def test_missing_runs_make_a_unit_not_comparable() -> None:
    comparison = compare(results("b", {A: [P, P], B: [P, P]}), results("c", {A: [P], B: [P, P]}))
    unit = next(u for u in comparison.units if u.scenario_id == A)
    assert unit.status is UnitStatus.NOT_COMPARABLE
    assert unit.reason == "candidate: 1 of 2 planned runs missing"


def test_unevaluated_runs_count_as_missing() -> None:
    comparison = compare(results("b", {A: [P]}), results("c", {A: [None]}))
    assert comparison.units[0].reason == "candidate: 1 of 1 planned runs missing"


def test_inconclusive_units_are_not_compared() -> None:
    comparison = compare(results("b", {A: [U], B: [P]}), results("c", {A: [F], B: [P]}))
    unit = next(u for u in comparison.units if u.scenario_id == A)
    assert unit.status is UnitStatus.NOT_COMPARABLE
    assert unit.reason == "baseline: inconclusive evaluation (1 run(s))"


def test_matched_success_uses_only_comparable_units() -> None:
    # Baseline A fails, but the candidate never ran A: A must not count as an improvement.
    comparison = compare(results("b", {A: [F], B: [P]}), results("c", {A: [], B: [P]}))
    assert comparison.matched_success_runs == ((1, 1), (1, 1))
    assert comparison.matched_success_drop() == 0


def test_units_planned_on_one_side_only() -> None:
    base = results("b", {A: [P], B: [P]})
    cand = results("c", {B: [P], C: [P]})
    units = {u.scenario_id: u for u in compare(base, cand).units}
    assert units[A].reason == "only planned in the baseline"
    assert units[C].reason == "only planned in the candidate"
    assert units[B].status is UnitStatus.UNCHANGED


def test_changed_benchmark_version_marks_changed_units_only() -> None:
    case = EVALUATION_SUITE.cases[0]
    target = EVALUATION_SUITE.scenarios[1]
    assert target.expectation_override is not None
    changed_scenario = target.updated(
        expectation_override=target.expectation_override.updated(
            scoring={"pass_threshold": 0.5}
        ).model_dump()
    )
    suite_v2 = EVALUATION_SUITE.updated(
        version="1.1.0",
        scenarios=[
            EVALUATION_SUITE.scenarios[0],
            changed_scenario,
            *EVALUATION_SUITE.scenarios[2:],
        ],
    )
    scenario_ids = [EVALUATION_SUITE.scenarios[0].id, target.id]
    base = results(
        "b",
        {sid: [P] for sid in scenario_ids},
        configuration=sample_configuration(BASELINE_VERSION, scenario_ids=scenario_ids),
    )
    cand = results(
        "c",
        {sid: [P] for sid in scenario_ids},
        configuration=sample_configuration(
            BASELINE_VERSION, scenario_ids=scenario_ids, suite=suite_v2
        ),
    )
    comparison = compare(base, cand)
    assert comparison.suite_changed
    units = {u.scenario_id: u for u in comparison.units}
    assert units[scenario_ids[0]].status is UnitStatus.UNCHANGED  # same definition
    assert units[target.id].status is UnitStatus.NOT_COMPARABLE
    assert "definition differs" in units[target.id].reason
    assert case.id == units[target.id].case_id


def test_experiments_without_a_snapshot_cannot_be_compared() -> None:
    legacy = results("b", {A: [P]}).model_copy(update={"configuration": None})
    with pytest.raises(ComparisonError, match="no configuration snapshot"):
        compare(legacy, results("c", {A: [P]}))


def test_configuration_and_evaluator_changes_are_reported() -> None:
    base = results("b", {A: [P]})
    cand = results(
        "c",
        {A: [P]},
        configuration=sample_configuration(VARIANT_VERSIONS["prompt-b"], scenario_ids=[A]),
    )
    comparison = compare(base, cand)
    assert {d.field for d in comparison.configuration_differences} == {
        "agent_version",
        "system_prompt",
    }
    assert not comparison.evaluator_changed and not comparison.suite_changed


def test_paired_test_needs_enough_discordant_units() -> None:
    six = {s: [P] for s in SCENARIOS[:6]}
    five_fail = {s: [F] if i < 5 else [P] for i, s in enumerate(SCENARIOS[:6])}
    six_fail = {s: [F] for s in SCENARIOS[:6]}
    five = compare(results("b", six), results("c", five_fail)).paired_test
    assert (five.newly_failing, five.significant) == (5, False)  # p = 0.0625
    all_six = compare(results("b", six), results("c", six_fail)).paired_test
    assert (all_six.newly_failing, all_six.significant) == (6, True)  # p = 0.03125


def test_metric_comparisons_mark_unavailable_sides() -> None:
    comparison = compare(results("b", {A: [P]}), results("c", {A: [P]}))
    cost = next(m for m in comparison.metrics if m.name == "cost_total")
    assert cost.delta is None
    assert cost.note and cost.note.startswith("baseline: cost unknown")
