import tempfile
import unittest
from pathlib import Path

from packages.knowledge.markdown import load_directory, load_markdown
from packages.knowledge.repository import scope_key

ROOT = Path(__file__).resolve().parents[1]


class MarkdownTests(unittest.TestCase):
    def load(self, path, root, versions=None):
        return load_markdown(path, root=root, product_versions=versions or ["1.0", "2.0"])

    def test_demo_batch(self):
        items = load_directory(ROOT / "data/demo/docs", root=ROOT, product_versions=["1.0", "2.0"])
        self.assertEqual(len(items), 8)
        self.assertEqual(len({item.document_key for item in items}), 8)
        self.assertEqual(items[0].source_path, "data/demo/docs/auth.md")

    def test_bom_line_endings_and_version_order(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = root / "quotas.md"
            body = (ROOT / "data/demo/docs/quotas.md").read_text(encoding="utf-8")
            p.write_text(body, encoding="utf-8")
            a = self.load(p, root)
            p.write_bytes(b"\xef\xbb\xbf" + body.replace("\n", "\r\n").encode())
            b = self.load(p, root, ["2.0", "1.0", "2.0"])
            self.assertEqual(a, b)

    def test_metadata_participates_in_revision(self):
        p = ROOT / "data/demo/docs/quotas.md"
        a = self.load(p, ROOT)
        b = self.load(p, ROOT, ["2.0"])
        self.assertEqual(a.content_hash, b.content_hash)
        self.assertNotEqual(a.revision_hash, b.revision_hash)

    def test_bad_metadata_and_encoding(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = root / "quotas.md"
            body = (ROOT / "data/demo/docs/quotas.md").read_text(encoding="utf-8")
            for value in (
                body.replace("Synthetic：true", "Synthetic：false"),
                body.replace("文档 ID：quotas", "文档 ID：wrong"),
                body + "\n文档 ID：quotas\n",
                body + "\x00",
            ):
                p.write_text(value, encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.load(p, root)
            p.write_bytes(b"\xff")
            with self.assertRaises(ValueError):
                self.load(p, root)

    def test_path_and_version_boundaries(self):
        p = ROOT / "data/demo/docs/quotas.md"
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                self.load(p, Path(d))
        for versions in ([], ["9.0"]):
            with self.assertRaises(ValueError):
                load_markdown(p, root=ROOT, product_versions=versions)

    def test_scope_validation(self):
        self.assertEqual(scope_key(None), "public")
        self.assertEqual(scope_key("tenant-alpha"), "tenant:tenant-alpha")
        with self.assertRaises(ValueError):
            scope_key("attacker")
