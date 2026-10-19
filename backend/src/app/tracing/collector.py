"""Builds the persisted trace of a run from a ``ScenarioRunResult``.

The collector only reads what the harness recorded (``ExecutionLog``, run record, effective
configuration, and the final answer). Every stored JSON payload and free-text field goes
through the redaction and size policy. Event IDs are derived from the run ID and sequence, so
collecting the same result twice yields identical events.
"""

from datetime import datetime

from pydantic import JsonValue

from app.domain.enums import RunStatus, ToolCallStatus
from app.domain.ids import IdPrefix, derived_id
from app.domain.trace import ToolCallRecord
from app.runner.runner import ScenarioRunResult
from app.runtime.execution import (
    DecisionStep,
    FaultInjectedStep,
    RejectedToolRequestStep,
    ToolCallStep,
)
from app.tracing.policy import DEFAULT_POLICY, TracePayloadPolicy, sanitize
from app.tracing.schema import CollectedTrace, EventKind, EventStatus, TraceEventRecord

_RESULT_STATUS = {
    ToolCallStatus.SUCCESS: EventStatus.OK,
    ToolCallStatus.ERROR: EventStatus.ERROR,
    ToolCallStatus.TIMEOUT: EventStatus.TIMEOUT,
}


def _error_type(record: ToolCallRecord) -> str | None:
    if record.status is ToolCallStatus.SUCCESS:
        return None
    if isinstance(record.output, dict) and isinstance(record.output.get("code"), str):
        return str(record.output["code"])
    if record.error_message:
        return record.error_message.split(":", 1)[0]
    return record.status.value


class TraceCollector:
    def __init__(self, policy: TracePayloadPolicy = DEFAULT_POLICY) -> None:
        self._policy = policy

    def collect(self, result: ScenarioRunResult) -> CollectedTrace:
        builder = _TraceBuilder(result, self._policy)
        return builder.build()


class _TraceBuilder:
    def __init__(self, result: ScenarioRunResult, policy: TracePayloadPolicy) -> None:
        self.result = result
        self.policy = policy
        self.events: list[TraceEventRecord] = []
        config = result.configuration
        self.faults_by_id = {fault.id: fault for fault in config.faults}

    # -- helpers ---------------------------------------------------------------------------

    def _next_id(self) -> str:
        return derived_id(IdPrefix.TRACE_EVENT, self.result.run.id, "event", str(len(self.events)))

    def _text(self, text: str) -> tuple[str, bool]:
        sanitized = sanitize(text, self.policy)
        value = sanitized.value
        return (value if isinstance(value, str) else str(value)), sanitized.truncated

    def _add(self, **fields: object) -> TraceEventRecord:
        event = TraceEventRecord.model_validate(
            {
                "id": self._next_id(),
                "experiment_id": self.result.run.experiment_id,
                "run_id": self.result.run.id,
                "sequence": len(self.events),
                **fields,
            }
        )
        self.events.append(event)
        return event

    def _payload_fields(self, value: JsonValue) -> dict[str, object]:
        sanitized = sanitize(value, self.policy)
        return {
            "payload": sanitized.value,
            "payload_truncated": sanitized.truncated,
            "payload_original_bytes": sanitized.original_bytes,
        }

    # -- build -----------------------------------------------------------------------------

    def build(self) -> CollectedTrace:
        result = self.result
        run = result.run
        config = result.configuration
        version = config.agent_version
        started = run.started_at or run.created_at
        finished = run.finished_at or started

        root = self._add(
            parent_id=None,
            kind=EventKind.RUN,
            status=self._run_status(),
            started_at=started,
            ended_at=finished,
            agent_name=config.agent.name,
            agent_version=config.agent.version,
            model_provider=version.model.provider,
            model_name=version.model.name,
            error_type=run.error.error_type if run.error else None,
            attributes={
                "scenario_id": config.scenario_id,
                "scenario_kind": config.scenario_kind.value,
                "run_status": run.status.value,
                "termination_reason": result.termination.reason.value,
                "seed": config.seed,
                "repetition": config.repetition,
            },
        )

        faults_by_call: dict[str, list[FaultInjectedStep]] = {}
        for step in result.execution.steps:
            if isinstance(step, FaultInjectedStep):
                faults_by_call.setdefault(step.tool_call_id, []).append(step)

        for step in result.execution.steps:
            if isinstance(step, ToolCallStep):
                self._tool_call(root, step.tool_call, faults_by_call.get(step.tool_call.id, []))
            elif isinstance(step, RejectedToolRequestStep):
                self._rejected(root, step)
            elif isinstance(step, DecisionStep):
                message, truncated = self._text(step.message)
                self._add(
                    parent_id=root.id,
                    kind=EventKind.AGENT_DECISION,
                    status=EventStatus.OK,
                    started_at=step.timestamp,
                    ended_at=step.timestamp,
                    payload=message,
                    payload_truncated=truncated,
                )
            # FaultInjectedStep is emitted under its tool call above.

        self._conclusion(root, finished)
        return CollectedTrace(experiment_id=run.experiment_id, run_id=run.id, events=self.events)

    def _run_status(self) -> EventStatus:
        status = self.result.run.status
        if status is RunStatus.COMPLETED:
            return EventStatus.OK
        if status is RunStatus.TIMED_OUT:
            return EventStatus.TIMEOUT
        return EventStatus.ERROR

    def _tool_call(
        self, root: TraceEventRecord, record: ToolCallRecord, faults: list[FaultInjectedStep]
    ) -> None:
        arguments = sanitize(record.arguments, self.policy)
        call = self._add(
            parent_id=root.id,
            kind=EventKind.TOOL_CALL,
            status=EventStatus.OK,
            started_at=record.started_at,
            ended_at=record.finished_at,
            tool_name=record.tool_name,
            tool_call_id=record.id,
            arguments=arguments.value,
            attributes={"arguments_truncated": arguments.truncated} if arguments.truncated else {},
        )
        for fault_step in faults:
            fault = self.faults_by_id[fault_step.fault_id]
            self._add(
                parent_id=call.id,
                kind=EventKind.FAULT_INJECTED,
                status=EventStatus.OK,
                started_at=fault_step.timestamp,
                ended_at=fault_step.timestamp,
                tool_name=record.tool_name,
                tool_call_id=record.id,
                fault_id=fault.id,
                fault_type=fault.type,
            )
        is_error = record.status is not ToolCallStatus.SUCCESS
        error_details: JsonValue = None
        if is_error:
            details = record.output if record.output is not None else record.error_message
            error_details = sanitize(details, self.policy).value
        self._add(
            parent_id=call.id,
            kind=EventKind.TOOL_RESULT,
            status=_RESULT_STATUS[record.status],
            started_at=record.finished_at,
            ended_at=record.finished_at,
            tool_name=record.tool_name,
            tool_call_id=record.id,
            error_type=_error_type(record),
            error_details=error_details,
            **(self._payload_fields(record.output) if not is_error else {}),
        )

    def _rejected(self, root: TraceEventRecord, step: RejectedToolRequestStep) -> None:
        name, _ = self._text(step.requested_name[:200])
        self._add(
            parent_id=root.id,
            kind=EventKind.TOOL_CALL,
            status=EventStatus.REJECTED,
            started_at=step.timestamp,
            ended_at=step.timestamp,
            tool_name=name,
            arguments=sanitize(step.arguments, self.policy).value,
            error_type=step.error.code.value,
            error_details=sanitize(step.error.model_dump(mode="json"), self.policy).value,
        )

    def _conclusion(self, root: TraceEventRecord, finished: datetime) -> None:
        result = self.result
        if result.output is not None:
            answer = result.output.answer
            content, truncated = self._text(answer.content)
            self._add(
                parent_id=root.id,
                kind=EventKind.FINAL_ANSWER,
                status=EventStatus.OK,
                started_at=finished,
                ended_at=finished,
                payload=content,
                payload_truncated=truncated,
                attributes={
                    "declared_outcome": answer.declared_outcome.value,
                    "source_ids": list(answer.source_ids),
                },
            )
            return
        termination = result.termination
        error = result.run.error
        self._add(
            parent_id=root.id,
            kind=EventKind.RUN_ERROR,
            status=self._run_status(),
            started_at=finished,
            ended_at=finished,
            error_type=error.error_type if error else termination.reason.value,
            error_details=sanitize(termination.model_dump(mode="json"), self.policy).value,
        )
