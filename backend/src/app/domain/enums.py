"""Constrained vocabularies used across the domain.

All enums are ``StrEnum`` so they serialise as their plain string value in JSON and JSON Schema.
"""

from enum import StrEnum


class Difficulty(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class ScenarioKind(StrEnum):
    """Whether a scenario runs the case unmodified or with injected faults."""

    BASELINE = "baseline"
    FAULT_INJECTED = "fault_injected"


class AgentOutcome(StrEnum):
    """Terminal outcome an agent declares in its final answer.

    Agents under test must report one of these alongside their answer so that expected
    behaviour (answer vs. abstain vs. report failure) can be scored deterministically.
    """

    ANSWERED = "answered"
    INSUFFICIENT_INFORMATION = "insufficient_information"
    TASK_FAILED = "task_failed"
    REFUSED = "refused"


class FaultType(StrEnum):
    """Kinds of fault that can be injected into a tool call."""

    TOOL_TIMEOUT = "tool_timeout"
    TOOL_ERROR = "tool_error"
    MALFORMED_OUTPUT = "malformed_output"
    EMPTY_RESULT = "empty_result"
    INCORRECT_OUTPUT = "incorrect_output"
    LATENCY = "latency"
    RATE_LIMIT = "rate_limit"
    PROMPT_INJECTION = "prompt_injection"


class MalformationKind(StrEnum):
    """How a ``malformed_output`` fault corrupts a tool response."""

    TRUNCATED = "truncated"
    INVALID_JSON = "invalid_json"
    WRONG_SCHEMA = "wrong_schema"


class ExperimentStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunStatus(StrEnum):
    """Execution status of a single run.

    This describes whether the run *executed*, not whether the agent succeeded at the task;
    task success is recorded in an ``EvaluationResult``. ``ERRORED`` means the harness or
    infrastructure failed, not the agent.
    """

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    ERRORED = "errored"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class FailureCategory(StrEnum):
    """Taxonomy of agent failure modes."""

    INCORRECT_ANSWER = "incorrect_answer"
    INCOMPLETE_ANSWER = "incomplete_answer"
    HALLUCINATED_CONTENT = "hallucinated_content"
    FABRICATED_CITATION = "fabricated_citation"
    UNHANDLED_TOOL_ERROR = "unhandled_tool_error"
    TOOL_MISUSE = "tool_misuse"
    EXCESSIVE_TOOL_CALLS = "excessive_tool_calls"
    PREMATURE_TERMINATION = "premature_termination"
    INSTRUCTION_VIOLATION = "instruction_violation"
    PROMPT_INJECTION_COMPLIANCE = "prompt_injection_compliance"
    UNWARRANTED_ABSTENTION = "unwarranted_abstention"
    TIMEOUT = "timeout"
    INFRASTRUCTURE_ERROR = "infrastructure_error"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ClassificationSource(StrEnum):
    """Who produced a failure classification."""

    RULE = "rule"
    HUMAN = "human"


class TraceEventType(StrEnum):
    MODEL_CALL = "model_call"
    TOOL_CALL = "tool_call"
    FAULT_INJECTED = "fault_injected"
    AGENT_MESSAGE = "agent_message"
    FINAL_ANSWER = "final_answer"
    ERROR = "error"


class ToolCallStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"


class ReviewTargetType(StrEnum):
    RUN = "run"
    EVALUATION_RESULT = "evaluation_result"
    FAILURE_CLASSIFICATION = "failure_classification"
    TRACE_EVENT = "trace_event"


class ReviewVerdict(StrEnum):
    AGREE = "agree"
    DISAGREE = "disagree"
    NEEDS_DISCUSSION = "needs_discussion"


class CriterionType(StrEnum):
    """Deterministic scoring criteria available to benchmark cases."""

    ANSWER_CONTAINS = "answer_contains"
    ANSWER_EXCLUDES = "answer_excludes"
    ANSWER_MATCHES_REGEX = "answer_matches_regex"
    DECLARED_OUTCOME = "declared_outcome"
    TOOL_CALL_COUNT = "tool_call_count"
    CITATIONS_WITHIN = "citations_within"


class MatchMode(StrEnum):
    ALL = "all"
    ANY = "any"
