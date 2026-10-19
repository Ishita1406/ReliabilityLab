"""Which agent implementation runs an experiment, resolved in API processes and workers alike.

An experiment's snapshot records the agent implementation (``AgentDescriptor``); a worker must
build exactly that implementation. Implementations register here; extra ones (e.g. test-only
agents) can be added with ``APP_AGENT_PLUGINS=module.a,module.b`` -- each module calls
``register`` on import.
"""

import importlib
from collections.abc import Callable
from dataclasses import dataclass

from app.agents.interface import Agent, AgentDescriptor
from app.config import Settings
from app.domain.agent import AgentVersion
from app.evaluation.engine import Evaluator
from app.evaluation.groq import judge_from_settings
from app.experiments.samples import AGENT_CONFIGS, RESEARCH_AGENT, research_agent_factory


class UnknownImplementationError(LookupError):
    """No registered implementation can run this agent version or descriptor."""


@dataclass(frozen=True)
class Implementation:
    descriptor: AgentDescriptor
    factory: Callable[[AgentVersion], Agent]
    supports: Callable[[AgentVersion], bool]


_REGISTRY: list[Implementation] = [
    Implementation(
        descriptor=RESEARCH_AGENT,
        factory=research_agent_factory,
        supports=lambda v: v.model.provider == "deterministic" and v.model.name in AGENT_CONFIGS,
    )
]


def register(implementation: Implementation) -> None:
    if any(i.descriptor == implementation.descriptor for i in _REGISTRY):
        return
    _REGISTRY.append(implementation)


def load_plugins(modules: str) -> None:
    for name in (m.strip() for m in modules.split(",")):
        if name:
            importlib.import_module(name)


def for_version(version: AgentVersion) -> Implementation:
    for implementation in _REGISTRY:
        if implementation.supports(version):
            return implementation
    raise UnknownImplementationError(
        f"no registered agent implementation for {version.model.provider}/{version.model.name}"
    )


def for_descriptor(descriptor: AgentDescriptor) -> Implementation:
    for implementation in _REGISTRY:
        if implementation.descriptor == descriptor:
            return implementation
    raise UnknownImplementationError(
        f"agent implementation {descriptor.name}@{descriptor.version} is not registered here"
    )


def default_evaluator(settings: Settings) -> Evaluator:
    """The evaluator new experiments are created with (and workers must match)."""
    return Evaluator(judge_from_settings(settings))
