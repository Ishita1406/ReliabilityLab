"""What metrics and comparisons consume: one record per run, grouped per experiment.

``ExperimentResults`` is built either in memory (``app.experiments.executor``) or from the
database (``app.db.evaluations.load_experiment_results``); everything downstream is a pure
function of it.
"""

from typing import Self

from pydantic import Field, NonNegativeInt, model_validator

from app.agents.interface import ModelUsage
from app.domain.base import DomainModel, require_unique
from app.domain.enums import RunStatus
from app.domain.ids import ExperimentId, RunId, Slug
from app.evaluation.models import CaseEvaluation
from app.experiments.configuration import ExperimentConfiguration


class RunRecord(DomainModel):
    run_id: RunId
    case_id: Slug
    scenario_id: Slug
    repetition: NonNegativeInt
    status: RunStatus
    duration_seconds: float | None = Field(
        ge=0, description="finished_at - started_at on the run's clock; null if unknown."
    )
    usage: list[ModelUsage] | None = Field(
        description="null: unknown (not reported, or the run produced no output)."
    )
    evaluation: CaseEvaluation | None = Field(description="null: the run was not evaluated.")

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.evaluation is not None and (
            self.evaluation.run_id != self.run_id or self.evaluation.scenario_id != self.scenario_id
        ):
            raise ValueError("evaluation belongs to a different run or scenario")
        return self


class ExperimentResults(DomainModel):
    experiment_id: ExperimentId
    configuration: ExperimentConfiguration | None = Field(
        description="null for experiments created before configuration snapshots existed."
    )
    runs: list[RunRecord]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        require_unique((r.run_id for r in self.runs), "run ids")
        require_unique((f"{r.scenario_id}#{r.repetition}" for r in self.runs), "run slots")
        config = self.configuration
        if config is not None:
            for record in self.runs:
                if record.scenario_id not in config.scenario_fingerprints:
                    raise ValueError(f"run '{record.run_id}' is for an unplanned scenario")
                if record.repetition >= config.repetitions:
                    raise ValueError(f"run '{record.run_id}' has an unplanned repetition")
                evaluation = record.evaluation
                if evaluation is not None and evaluation.evaluator_version != (
                    config.evaluator_version
                ):
                    raise ValueError(
                        f"run '{record.run_id}' was evaluated by '{evaluation.evaluator_version}'"
                        f", not the experiment's '{config.evaluator_version}'"
                    )
        return self

    @property
    def planned_runs(self) -> int | None:
        config = self.configuration
        return None if config is None else len(config.scenario_ids) * config.repetitions

    @property
    def evaluated(self) -> list[RunRecord]:
        return [r for r in self.runs if r.evaluation is not None]

    def for_scenario(self, scenario_id: str) -> list[RunRecord]:
        return [r for r in self.runs if r.scenario_id == scenario_id]
