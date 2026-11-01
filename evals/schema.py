"""Day 2 development fixtures, not a frozen test set."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(min_length=1)
    scenario_group: str = Field(min_length=1)
    question: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    product_version: str | None
    relevant_sources: list[str]
    expected_behavior: str = Field(min_length=1)
    forbidden_actions: list[str] = Field(min_length=1)
    category: Literal[
        "knowledge",
        "troubleshooting",
        "missing_info",
        "unknown",
        "authorization",
        "approval",
        "injection",
    ]
    answerability: Literal["answer", "clarify", "abstain", "refuse"]
    synthetic: Literal[True]

    @model_validator(mode="after")
    def require_answer_evidence(self):
        if self.answerability == "answer" and not self.relevant_sources:
            raise ValueError("answer requires relevant_sources")
        return self
