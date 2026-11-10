import json
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from packages.agent.knowledge_mock import KnowledgeScenarioMock
from packages.agent.loop import AgentLoop
from packages.agent.models import ModelResponse, ToolCall
from packages.tools.executor import ToolExecutor
from packages.tools.schemas import AuthContext, DocEvidence, SearchDocsResult

QUESTION = "starter配额是多少？"
ALPHA = AuthContext(user_id="user-alpha", tenant_id="tenant-alpha", role="member")


def evidence(key, body):
    return DocEvidence(
        chunk_id=key,
        source="quotas#limits",
        body=body,
        source_path="data/demo/docs/quotas.md",
        version_number=1,
        product_version="2.0",
        start_line=8,
        end_line=10,
    )


class GroundingTests(IsolatedAsyncioTestCase):
    async def test_answer_renders_only_verified_excerpt(self):
        source = evidence("first", "starter 每分钟60次。")
        with patch(
            "packages.tools.knowledge.search_docs", return_value=SearchDocsResult(sources=[source])
        ) as search:
            run = await AgentLoop(KnowledgeScenarioMock(QUESTION, "2.0"), ToolExecutor()).run(
                QUESTION, context=ALPHA, grounded=True
            )
        self.assertEqual(search.call_args.args[1], "tenant-alpha")
        self.assertEqual((run.reason, run.tool_calls), ("answer", 1))
        self.assertEqual(run.citations, [source.model_dump()])
        self.assertTrue(run.answer.endswith(source.body))

    async def test_missing_version_and_empty_or_conflicting_evidence(self):
        for version, rows, reason in (
            (None, [], "clarification"),
            ("2.0", [], "insufficient_evidence"),
            ("2.0", [evidence("one", "60次"), evidence("two", "90次")], "conflicting_evidence"),
        ):
            with (
                self.subTest(reason=reason),
                patch(
                    "packages.tools.knowledge.search_docs",
                    return_value=SearchDocsResult(sources=rows),
                ) as search,
            ):
                run = await AgentLoop(KnowledgeScenarioMock(QUESTION, version), ToolExecutor()).run(
                    QUESTION, context=ALPHA, grounded=True
                )
            self.assertEqual(run.reason, reason)
            self.assertEqual(run.citations, [])
            self.assertEqual(search.call_count, int(version is not None))

    async def test_unknown_and_stale_citations_fail_closed(self):
        for invalid in ("unknown", "old"):
            responses = [
                ModelResponse(
                    text="",
                    mode="mock",
                    model="test",
                    tool_calls=[
                        ToolCall(
                            call_id=f"search-{i}",
                            name="search_docs",
                            arguments={"question": f"query {i}", "product_version": "2.0"},
                        )
                    ],
                )
                for i in range(2)
            ]
            responses.append(
                ModelResponse(
                    text=json.dumps({"status": "answered", "citation_ids": [invalid]}),
                    mode="mock",
                    model="test",
                )
            )

            class Provider:
                async def generate(self, request):
                    return responses.pop(0)

            with patch(
                "packages.tools.knowledge.search_docs",
                side_effect=[
                    SearchDocsResult(sources=[evidence("old", "old body")]),
                    SearchDocsResult(sources=[evidence("current", "current body")]),
                ],
            ):
                run = await AgentLoop(Provider(), ToolExecutor()).run(
                    QUESTION, context=ALPHA, grounded=True
                )
            self.assertEqual(
                (run.status, run.reason, run.citations), ("failed", "invalid_citations", [])
            )
            self.assertNotIn("old body", run.answer)
