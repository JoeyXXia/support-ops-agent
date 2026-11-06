import unittest

from packages.knowledge.chunking import split_markdown


class ChunkingTests(unittest.TestCase):
    def assert_traceable(self, body):
        chunks = split_markdown(body)
        self.assertEqual(chunks, split_markdown(body))
        for chunk in chunks:
            self.assertEqual(body[chunk.start_char : chunk.end_char], chunk.body)
            self.assertLessEqual(len(chunk.body), 800)
            self.assertEqual(chunk.start_line, body.count("\n", 0, chunk.start_char) + 1)
        return chunks

    def test_headings_positions_and_duplicate_titles(self):
        chunks = self.assert_traceable(
            "# 产品\n\n介绍。\n\n## 限额\n\n60次。\n\n### 细节\n\n等待。\n\n## 限额\n\n另一章。\n"
        )
        self.assertEqual(
            [c.heading_path for c in chunks],
            [("产品",), ("产品", "限额"), ("产品", "限额", "细节"), ("产品", "限额")],
        )
        self.assertNotEqual(chunks[1].start_char, chunks[3].start_char)

    def test_fenced_code_is_not_heading_or_split(self):
        body = "# 根\n\n```python\n# 不是标题\n\nx = 1\n```\n\n文字\n"
        chunks = self.assert_traceable(body)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].heading_path, ("根",))

    def test_long_paragraph_overlap_within_section(self):
        body = "x" * 1700
        chunks = self.assert_traceable(body)
        self.assertEqual([c.start_char for c in chunks], [0, 700, 1400])
        self.assertEqual(chunks[0].body[-100:], chunks[1].body[:100])

    def test_protected_oversize_and_unclosed_fence(self):
        for body in ("```\n" + "x" * 801 + "\n```\n", "| a | b |\n" * 150, "```\nabc"):
            with self.assertRaises(ValueError):
                split_markdown(body)

    def test_short_table_and_tilde_fence(self):
        body = "# 规则\n\n| key | value |\n| --- | --- |\n| rate | 60 |\n\n~~~\n## literal\n~~~\n"
        self.assertEqual(len(self.assert_traceable(body)), 1)

    def test_empty_and_normalization_boundary(self):
        for body in (" ", "# a\r\n", "\ufeff# a\n"):
            with self.assertRaises(ValueError):
                split_markdown(body)

    def test_input_hash_includes_heading_path(self):
        a = split_markdown("# A\n\n" + "x" * 900)[-1]
        b = split_markdown("# B\n\n" + "x" * 900)[-1]
        self.assertEqual(a.content_hash, b.content_hash)
        self.assertNotEqual(a.input_hash, b.input_hash)

    def test_heading_hash_is_not_removed_from_language_name(self):
        chunks = split_markdown("# C#\n\ntext\n\n## FAQ ###\n\nmore\n")
        self.assertEqual(chunks[0].heading_path, ("C#",))
        self.assertEqual(chunks[1].heading_path, ("C#", "FAQ"))
