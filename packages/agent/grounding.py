"""Validate citation membership and render evidence-only demo answers."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class GroundedDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["answered", "clarify", "insufficient", "conflict"]
    citation_ids: list[str] = Field(default_factory=list, max_length=5)


def grounded_answer(text, evidence):
    decision = GroundedDecision.model_validate_json(text)
    ids = decision.citation_ids
    if decision.status == "answered":
        if not ids or len(set(ids)) != len(ids) or any(key not in evidence for key in ids):
            raise ValueError("invalid_citations")
        citations = [evidence[key] for key in ids]
        answer = "依据当前文档摘录：\n\n" + "\n\n".join(citation["body"] for citation in citations)
        return "answer", answer, citations
    if ids:
        raise ValueError("unexpected_citations")
    if decision.status == "clarify":
        return "clarification", "请先补充产品版本，再查询适用文档。", []
    if decision.status == "conflict":
        return "conflicting_evidence", "检索到的同名来源内容不一致，暂无法给出确定结论。", []
    return "insufficient_evidence", "当前文档没有足够依据，暂无法给出确定结论。", []
