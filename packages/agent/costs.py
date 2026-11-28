"""Record usage provenance and estimates under a versioned, operator-supplied USD catalog."""

import os
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from .models import ModelResponse, Usage

MILLION = Decimal(1000000)


class Price(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1)
    effective_on: date
    input_per_million: Decimal = Field(ge=0)
    output_per_million: Decimal = Field(ge=0)


class Catalog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    currency: Literal["USD"] = "USD"
    prices: list[Price] = Field(default_factory=list)


class ModelCharge(BaseModel):
    model: str | None
    usage_source: Literal["provider_reported", "mock_fixture", "unavailable"]
    usage: Usage | None
    effective_on: date | None = None
    estimated_usd: Decimal | None = None

    @field_serializer("effective_on", "estimated_usd")
    def serialize_charge(self, value):
        return str(value) if value is not None else None


class Accounting(BaseModel):
    currency: Literal["USD"] = "USD"
    calls: list[ModelCharge] = Field(default_factory=list)
    estimated_total_usd: Decimal | None = None

    @field_serializer("estimated_total_usd")
    def serialize_total(self, value):
        return str(value) if value is not None else None


def record_progress(record, event):
    if "accounting" in event:
        record["accounting"] = event["accounting"]
        event = {key: value for key, value in event.items() if key != "accounting"}
    record["events"].append(event)


class CostPolicy(BaseModel):
    catalog: Catalog = Field(default_factory=Catalog)
    budget_usd: Decimal | None = Field(default=None, gt=0)
    as_of: date = Field(default_factory=lambda: datetime.now(UTC).date())

    @classmethod
    def configured(cls):
        path = os.getenv("SUPPORTOPS_PRICE_CATALOG")
        budget = os.getenv("SUPPORTOPS_RUN_BUDGET_USD")
        catalog = Catalog.model_validate_json(Path(path).read_bytes()) if path else Catalog()
        keys = {(row.model, row.effective_on) for row in catalog.prices}
        if len(keys) != len(catalog.prices):
            raise ValueError("Duplicate price for model and date")
        return cls(catalog=catalog, budget_usd=budget)

    def record(self, accounting: Accounting, response: ModelResponse | None):
        usage = response.usage if response else None
        charge = ModelCharge(
            model=response.model if response else None,
            usage=usage,
            usage_source=(
                ("provider_reported" if response.mode == "live" else "mock_fixture")
                if usage
                else "unavailable"
            ),
        )
        if usage:
            price = max(
                (
                    row
                    for row in self.catalog.prices
                    if row.model == response.model and row.effective_on <= self.as_of
                ),
                key=lambda row: row.effective_on,
                default=None,
            )
            if price:
                charge.effective_on = price.effective_on
                charge.estimated_usd = (
                    usage.input_tokens * price.input_per_million
                    + usage.output_tokens * price.output_per_million
                ) / MILLION
        accounting.calls.append(charge)
        amounts = [row.estimated_usd for row in accounting.calls]
        accounting.estimated_total_usd = sum(amounts, Decimal(0)) if None not in amounts else None
        if self.budget_usd is not None:
            if accounting.estimated_total_usd is None:
                return "cost_unknown"
            if accounting.estimated_total_usd >= self.budget_usd:
                return "cost_budget"
        return None
