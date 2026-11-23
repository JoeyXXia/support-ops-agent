import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from scripts.freeze_day29 import ROOT, Cluster, distinct_partitions, freeze, materialize


class Day29FreezeTests(unittest.TestCase):
    def test_frozen_manifest_verifies_and_recreates_identical_bytes(self):
        files = freeze(ROOT)
        materialize(files, ROOT / "evals/day29")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            materialize(files, output, write=True)
            materialize(freeze(ROOT), output)
            self.assertEqual((output / "final.jsonl").read_bytes(), files["final.jsonl"])

    def test_cross_partition_near_duplicate_and_legacy_overlap_fail(self):
        first = {"id": "a", "question": "请核对产品版本的配额信息"}
        second = {"id": "b", "question": "请核对产品版本的配额信息吧"}
        with self.assertRaisesRegex(ValueError, "Near duplicate"):
            distinct_partitions({"dev": [first], "final": [second]}, [])
        with self.assertRaisesRegex(ValueError, "Near duplicate"):
            distinct_partitions({"dev": [], "final": [second]}, [first["question"]])

    def test_write_cannot_replace_frozen_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            target = output / "final.jsonl"
            target.write_bytes(b"original")
            with self.assertRaisesRegex(ValueError, "Frozen content differs"):
                materialize({"final.jsonl": b"changed"}, output, write=True)
            self.assertEqual(target.read_bytes(), b"original")

    def test_answer_without_evidence_and_auth_without_refusal_fail(self):
        case = {
            "bucket": "qa",
            "partition": "dev",
            "group": "synthetic-example",
            "outcome": "answer",
            "sources": [],
            "expected": "仍然必须先核对有效来源。",
            "questions": ["这条合成问题需要来源吗？"],
        }
        with self.assertRaisesRegex(ValidationError, "requires evidence"):
            Cluster.model_validate(case)
        with self.assertRaisesRegex(ValidationError, "define a refusal"):
            Cluster.model_validate(dict(case, bucket="auth", outcome="execute"))
