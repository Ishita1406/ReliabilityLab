"""Cost estimation from reported model usage.

A run's cost is *known* only when the agent reported its usage (``usage`` is not null) and
every model it used has a price. Otherwise the cost is unknown and reported as unavailable,
never as zero. A run that reported ``usage=[]`` (no model calls) has a known cost of zero.

Prices are configuration, not facts baked into code: no default price list is shipped, because
provider prices change. Supply a ``PricingTable`` (e.g. from a JSON file) when estimating cost.
"""

from typing import Self

from pydantic import Field, model_validator

from app.agents.interface import ModelUsage
from app.domain.base import DomainModel, NonEmptyStr, require_unique
from app.domain.ids import Slug


class ModelPrice(DomainModel):
    provider: Slug
    model: NonEmptyStr
    input_usd_per_million_tokens: float = Field(ge=0)
    output_usd_per_million_tokens: float = Field(ge=0)


class PricingTable(DomainModel):
    prices: list[ModelPrice] = Field(default_factory=list)
    source: NonEmptyStr | None = Field(default=None, description="Where the prices came from.")

    @model_validator(mode="after")
    def _unique(self) -> Self:
        require_unique((f"{p.provider}/{p.model}" for p in self.prices), "priced models")
        return self

    def price(self, provider: str, model: str) -> ModelPrice | None:
        for price in self.prices:
            if (price.provider, price.model) == (provider, model):
                return price
        return None


def run_cost(usage: list[ModelUsage] | None, pricing: PricingTable) -> tuple[float | None, str]:
    """``(cost_usd, reason)``; cost is ``None`` when unknown, with the reason."""
    if usage is None:
        return None, "usage not reported"
    total = 0.0
    for item in usage:
        price = pricing.price(item.provider, item.model)
        if price is None:
            return None, f"no price for {item.provider}/{item.model}"
        total += (
            item.input_tokens * price.input_usd_per_million_tokens
            + item.output_tokens * price.output_usd_per_million_tokens
        ) / 1_000_000
    return total, "priced" if usage else "no model calls"
