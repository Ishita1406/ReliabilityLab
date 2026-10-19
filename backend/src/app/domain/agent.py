"""Agent configurations and their versions.

An ``AgentConfig`` is a named agent under test (e.g. "research-assistant"). Each change to its
model, prompt, tools, or parameters produces a new ``AgentVersion``. Experiments always pin an
``AgentVersion`` so results stay attributable to an exact configuration.
"""

from typing import Self

from pydantic import Field, PositiveFloat, PositiveInt, model_validator

from app.domain.base import DomainModel, NonEmptyStr, require_unique
from app.domain.ids import AgentConfigId, AgentVersionId, IdPrefix, Slug, new_id
from app.domain.timestamps import UtcDateTime, utc_now


class AgentConfig(DomainModel):
    id: AgentConfigId = Field(default_factory=lambda: new_id(IdPrefix.AGENT_CONFIG))
    name: Slug
    description: NonEmptyStr | None = None
    created_at: UtcDateTime = Field(default_factory=utc_now)


class ModelSpec(DomainModel):
    provider: Slug = Field(description="Model provider, e.g. 'anthropic' or 'mock'.")
    name: NonEmptyStr = Field(description="Provider-specific model identifier.")


class AgentParameters(DomainModel):
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_steps: PositiveInt = Field(description="Maximum reasoning/tool steps per run.")
    timeout_seconds: PositiveFloat = Field(description="Wall-clock limit per run.")


class AgentVersion(DomainModel):
    id: AgentVersionId = Field(default_factory=lambda: new_id(IdPrefix.AGENT_VERSION))
    agent_config_id: AgentConfigId
    version_label: Slug = Field(description="Human-readable label, unique per agent config.")
    model: ModelSpec
    system_prompt: NonEmptyStr
    tools: list[Slug] = Field(default_factory=list, description="Tools the agent may call.")
    parameters: AgentParameters
    created_at: UtcDateTime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _tools_unique(self) -> Self:
        require_unique(self.tools, "tools")
        return self
