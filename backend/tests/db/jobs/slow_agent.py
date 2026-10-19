"""TEST-ONLY agent plugin: the deterministic research agent, slowed down by a real sleep per
run, so a worker can be killed or cancelled part-way through an experiment.

Loaded by workers via ``APP_AGENT_PLUGINS=tests.db.jobs.slow_agent``.
"""

import os
import time

from app.agents.interface import Agent, AgentDescriptor, AgentEnvironment, AgentOutput, AgentTask
from app.agents.research import DeterministicResearchAgent
from app.domain.agent import AgentVersion
from app.experiments.samples import agent_version
from app.jobs.registry import Implementation, register

SLOW_AGENT = AgentDescriptor(name="slow-research", version="0.0.1")
SLOW_VERSION = agent_version("0.0.1-slow", model_name="regex-extractor-slow")
SECONDS_PER_RUN = float(os.environ.get("SLOW_AGENT_SECONDS", "0.3"))


class SlowResearchAgent(DeterministicResearchAgent):
    @property
    def descriptor(self) -> AgentDescriptor:
        return SLOW_AGENT

    def run(self, task: AgentTask, environment: AgentEnvironment) -> AgentOutput:
        time.sleep(SECONDS_PER_RUN)
        return super().run(task, environment)


def factory(version: AgentVersion) -> Agent:
    return SlowResearchAgent()


register(
    Implementation(
        descriptor=SLOW_AGENT,
        factory=factory,
        supports=lambda v: v.model.name == "regex-extractor-slow",
    )
)
