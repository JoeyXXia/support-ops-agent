import json
import unittest
from pathlib import Path

from evals.behavior import ExpectedRun, score_behavior, score_retrieval
from scripts.score_day30 import score_rows

ROOT = Path(__file__).resolve().parents[1]


class BehaviorTests(unittest.TestCase):
    def setUp(self):
        fixtures = json.loads((ROOT / "evals/day30/calibration.json").read_text(encoding="utf-8"))
        self.samples = {sample["sample_id"]: sample for sample in fixtures["samples"]}
        self.cases = {
            row["id"]: row
            for row in map(json.loads, (ROOT / "evals/day29/dev.jsonl").read_bytes().splitlines())
        }

    def test_export_matches_calibration_and_detects_each_intentional_failure(self):
        results = score_rows(self.cases, self.samples.values())
        saved = json.loads((ROOT / "reports/day-30-calibration.json").read_text(encoding="utf-8"))
        self.assertEqual(results, saved["results"])
        actual = {row["sample_id"]: row["behavior"] for row in results}
        expected = {
            "wrong-tool": "unexpected_tool_sequence",
            "boolean-schema-error": "invalid_tool_arguments",
            "wrong-valid-parameter": "unexpected_tool_parameters",
            "step-budget": "step_budget_exceeded",
            "claimed-approval-without-commit": "unexpected_committed_ticket_count",
            "duplicate-write": "unexpected_committed_ticket_count",
            "wrong-final-ticket-state": "unexpected_ticket_fields",
        }
        for name, result in actual.items():
            with self.subTest(name=name):
                self.assertEqual(result["passed"], name not in expected)
                if name in expected:
                    self.assertIn(expected[name], result["reasons"])

    def test_missing_evidence_or_receipt_cannot_pass(self):
        sample = self.samples["approved-commit"]
        rule = ExpectedRun.model_validate(sample["expected"])
        obs = sample["observation"]
        self.assertIn(
            "missing_durable_evidence",
            score_behavior(rule, dict(obs, committed_tickets=None))["reasons"],
        )
        self.assertIn(
            "ticket_receipt_mismatch",
            score_behavior(rule, dict(obs, receipt_ticket_ids=[]))["reasons"],
        )
        self.assertIn(
            "write_budget_exceeded", score_behavior(rule, dict(obs, write_calls=4))["reasons"]
        )

    def test_retries_count_attempts_without_inventing_extra_logical_calls(self):
        sample = self.samples["read-ok"]
        rule = ExpectedRun.model_validate(sample["expected"])
        obs = sample["observation"]
        attempt = obs["attempts"][0]
        repeat = dict(obs, tool_calls=2, attempts=[dict(attempt, ok=False), attempt])
        self.assertTrue(score_behavior(rule, repeat)["passed"])
        self.assertIn(
            "tool_attempt_count_mismatch",
            score_behavior(rule, dict(repeat, tool_calls=1))["reasons"],
        )
        self.assertIn(
            "invalid_tool_retry",
            score_behavior(rule, dict(repeat, attempts=[attempt, attempt]))["reasons"],
        )

    def test_retrieval_ignores_private_and_does_not_grade_missing_labels(self):
        case = self.cases["grounded-cite-01"]
        items = [
            {"scope_key": "tenant:tenant-alpha", "source": "quotas#limits"},
            {"scope_key": "public", "source": "quotas#limits"},
        ]
        self.assertFalse(score_retrieval(case, items, k=1)["hit_at_k"])
        self.assertEqual(score_retrieval(case, items, k=2)["reciprocal_rank_at_k"], 0.5)
        self.assertIn("excluded", score_retrieval(self.cases["usage-alpha-01"], items))

    def test_unrecognized_or_duplicate_ids_fail_export(self):
        sample = self.samples["read-ok"]
        with self.assertRaisesRegex(ValueError, "Duplicate sample"):
            score_rows(self.cases, [sample, sample])
        with self.assertRaisesRegex(ValueError, "Not a Day29 development case"):
            score_rows(
                self.cases, [dict(sample, expected=dict(sample["expected"], case_id="not-dev"))]
            )
