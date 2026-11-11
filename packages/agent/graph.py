"""Explicit in-process routing for the support agent."""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context

from .loop import AgentLoop, AgentTurn


class SupportState(TypedDict):
    turn: AgentTurn
    path: list[str]
    next_node: str


class GraphAgent(AgentLoop):
    def __init__(self, provider, executor, **limits):
        super().__init__(provider, executor, **limits)
        graph = StateGraph(SupportState)
        graph.add_node("classify", self._classify)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("call_tool", self._call_tool)
        graph.add_node("answer", self._answer)
        graph.add_node("clarify", self._clarify)
        graph.add_node("done", self._done)
        graph.add_edge(START, "classify")
        for node, destinations in (
            ("classify", ("retrieve", "call_tool", "answer", "done")),
            ("retrieve", ("classify", "done")),
            ("call_tool", ("classify", "done")),
            ("answer", ("clarify", "done")),
        ):
            graph.add_conditional_edges(node, self._route, {name: name for name in destinations})
        graph.add_edge("clarify", "done")
        graph.add_edge("done", END)
        self.graph = graph.compile()

    @staticmethod
    def _route(state: SupportState):
        return state["next_node"]

    async def _classify(self, state: SupportState):
        next_node = await self.classify(state["turn"])
        return {"path": [*state["path"], "classify"], "next_node": next_node}

    async def _tools(self, state: SupportState, node: str):
        turn = state["turn"]
        succeeded = await self.call_tools(turn)
        next_node = "classify" if succeeded and turn.result.model_calls < self.max_steps else "done"
        return {"path": [*state["path"], node], "next_node": next_node}

    async def _retrieve(self, state: SupportState):
        return await self._tools(state, "retrieve")

    async def _call_tool(self, state: SupportState):
        return await self._tools(state, "call_tool")

    def _answer(self, state: SupportState):
        turn = state["turn"]
        self.answer(turn)
        next_node = "clarify" if turn.result.reason == "clarification" else "done"
        return {"path": [*state["path"], "answer"], "next_node": next_node}

    @staticmethod
    def _clarify(state: SupportState):
        return {"path": [*state["path"], "clarify"]}

    @staticmethod
    def _done(state: SupportState):
        return {"path": [*state["path"], "done"]}

    async def run(self, question, *, context, on_event=None, grounded=False):
        turn = self.start(question, context=context, on_event=on_event, grounded=grounded)
        with tracing_context(enabled=False):
            final = await self.graph.ainvoke(
                {"turn": turn, "path": [], "next_node": "classify"},
                config={"recursion_limit": 2 * self.max_steps + 5},
            )
        turn.result.state_path = final["path"]
        return turn.result
