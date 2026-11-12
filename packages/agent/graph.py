"""Explicit in-process routing for the support agent."""

import json
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from langsmith import tracing_context

from packages.tools.schemas import AuthContext

from .loop import AgentLoop, AgentTurn, RunResult
from .models import ModelRequest, ModelResponse


class SupportState(TypedDict):
    turn: dict
    path: list[str]
    next_node: str


class GraphAgent(AgentLoop):
    def __init__(self, provider, executor, *, checkpointer=None, **limits):
        super().__init__(provider, executor, **limits)
        graph = StateGraph(SupportState)
        graph.add_node("classify", self._classify)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("call_tool", self._call_tool)
        graph.add_node("answer", self._answer)
        graph.add_node("clarify", self._clarify)
        graph.add_node("done", self._done)
        graph.add_node("await_approval", self._await_approval)
        graph.add_node("execute", self._execute)
        graph.add_edge(START, "classify")
        for node, destinations in (
            ("classify", ("retrieve", "call_tool", "answer", "done")),
            ("retrieve", ("classify", "done")),
            ("call_tool", ("classify", "done")),
            ("answer", ("clarify", "await_approval", "done")),
        ):
            graph.add_conditional_edges(node, self._route, {name: name for name in destinations})
        graph.add_edge("clarify", "done")
        graph.add_edge("await_approval", "execute")
        graph.add_edge("execute", "done")
        graph.add_edge("done", END)
        self.graph = graph.compile(checkpointer=checkpointer)
        self.on_event = None
        self.durability = "sync" if checkpointer else None

    @staticmethod
    def _route(state: SupportState):
        return state["next_node"]

    async def _classify(self, state: SupportState):
        turn = self._restore(state["turn"])
        next_node = await self.classify(turn)
        return {
            "turn": self._snapshot(turn),
            "path": [*state["path"], "classify"],
            "next_node": next_node,
        }

    async def _tools(self, state: SupportState, node: str):
        turn = self._restore(state["turn"])
        succeeded = await self.call_tools(turn)
        next_node = "classify" if succeeded and turn.result.model_calls < self.max_steps else "done"
        return {
            "turn": self._snapshot(turn),
            "path": [*state["path"], node],
            "next_node": next_node,
        }

    async def _retrieve(self, state: SupportState):
        return await self._tools(state, "retrieve")

    async def _call_tool(self, state: SupportState):
        return await self._tools(state, "call_tool")

    def _answer(self, state: SupportState):
        turn = self._restore(state["turn"])
        self.answer(turn)
        next_node = "clarify" if turn.result.reason == "clarification" else "done"
        if turn.result.proposed_action and self.graph.checkpointer:
            turn.result.status, turn.result.reason = "awaiting_approval", "pending_approval"
            next_node = "await_approval"
        return {
            "turn": self._snapshot(turn),
            "path": [*state["path"], "answer"],
            "next_node": next_node,
        }

    def _approval_repository(self):
        from packages.storage.tickets import TicketRepository

        return TicketRepository(self.executor.engine)

    def _await_approval(self, state):
        turn = self._restore(state["turn"])
        proposal = self._approval_repository().get_approval(
            turn.result.proposed_action["approval_id"], turn.context
        )
        if proposal.status == "pending":
            interrupt(proposal.model_dump(mode="json"))
        return {"path": [*state["path"], "await_approval"]}

    def _execute(self, state):
        from packages.storage.tickets import ApprovalConflict

        turn = self._restore(state["turn"])
        repository = self._approval_repository()
        approval_id = turn.result.proposed_action["approval_id"]
        proposal = repository.get_approval(approval_id, turn.context)
        if proposal.status == "rejected":
            turn.result.status, turn.result.reason = "completed", "approval_rejected"
            turn.result.answer = "已拒绝工单草稿。"
        else:
            try:
                ticket = repository.create_ticket(approval_id, turn.context)
                turn.result.ticket = ticket.model_dump(mode="json")
                turn.result.status, turn.result.reason = "completed", "ticket_created"
                turn.result.answer = "工单已创建。"
            except (ApprovalConflict, PermissionError):
                turn.result.status, turn.result.reason = "failed", "approval_invalid"
                turn.result.answer = "审批已失效，请生成新的草稿。"
        turn.result.proposed_action = repository.get_approval(approval_id, turn.context).model_dump(
            mode="json"
        )
        return {"turn": self._snapshot(turn), "path": [*state["path"], "execute"]}

    @staticmethod
    def _clarify(state: SupportState):
        return {"path": [*state["path"], "clarify"]}

    @staticmethod
    def _done(state: SupportState):
        return {"path": [*state["path"], "done"]}

    @staticmethod
    def _snapshot(turn):
        return {
            "request": turn.request.model_dump(mode="json"),
            "context": turn.context.model_dump(mode="json"),
            "grounded": turn.grounded,
            "result": turn.result.model_dump(mode="json"),
            "seen": [list(item) for item in sorted(turn.seen)],
            "call_ids": sorted(turn.call_ids),
            "evidence": turn.evidence,
            "response": turn.response.model_dump(mode="json") if turn.response else None,
        }

    def _restore(self, value):
        return AgentTurn(
            request=ModelRequest.model_validate(value["request"]),
            context=AuthContext.model_validate_json(json.dumps(value["context"])),
            grounded=value["grounded"],
            on_event=self.on_event,
            result=RunResult.model_validate(value["result"]),
            seen={tuple(item) for item in value["seen"]},
            call_ids=set(value["call_ids"]),
            evidence=value["evidence"],
            response=ModelResponse.model_validate(value["response"]) if value["response"] else None,
        )

    async def run(
        self,
        question,
        *,
        context,
        on_event=None,
        grounded=False,
        thread_id=None,
        resume=False,
        approval_ready=False,
    ):
        self.on_event = on_event
        config = {"recursion_limit": 2 * self.max_steps + 5}
        if thread_id:
            config["configurable"] = {"thread_id": thread_id}
        initial = None
        if not resume:
            turn = self.start(question, context=context, on_event=on_event, grounded=grounded)
            initial = {"turn": self._snapshot(turn), "path": [], "next_node": "classify"}
        if approval_ready:
            initial = Command(resume=True)
        with tracing_context(enabled=False):
            final = await self.graph.ainvoke(initial, config=config, durability=self.durability)
        result = self._restore(final["turn"]).result
        result.state_path = final["path"]
        if final.get("__interrupt__"):
            result.state_path = [*result.state_path, "await_approval"]
        return result
