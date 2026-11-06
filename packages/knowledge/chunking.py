"""Deterministic ATX-heading/paragraph splitter with exact normalized-source offsets."""

import hashlib
import re
from dataclasses import dataclass

POLICY = "heading-paragraph-v1:800:100"
INPUT_VERSION = "heading-prefix-v1"
MAX_CHARS = 800
OVERLAP = 100


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    body: str
    start_char: int
    end_char: int
    start_line: int
    end_line: int
    heading_path: tuple[str, ...]
    content_hash: str
    embedding_input: str
    input_hash: str


def split_markdown(body: str) -> list[Chunk]:
    if not body.strip():
        raise ValueError("empty_document")
    if "\r" in body or body.startswith("\ufeff"):
        raise ValueError("normalized_document_required")
    # Blocks are intervals into the original normalized body, never reassembled text.
    lines = body.splitlines(keepends=True)
    sections = []
    headings = []
    offset = 0
    section_start = 0
    fence = None
    for line in lines:
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\n"))
        if fence:
            if (
                marker
                and marker[1][0] == fence[0]
                and len(marker[1]) >= fence[1]
                and not marker[2].strip()
            ):
                fence = None
        elif marker:
            fence = (marker[1][0], len(marker[1]))
        else:
            heading = re.match(r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*$", line.rstrip("\n"))
            if heading:
                if offset > section_start:
                    sections.append((section_start, offset, tuple(text for _, text in headings)))
                level = len(heading[1])
                headings = [(n, text) for n, text in headings if n < level]
                headings.append((level, re.sub(r"[ \t]+#+[ \t]*$", "", heading[2])))
                section_start = offset
        offset += len(line)
    if fence:
        raise ValueError("unclosed_code_fence")
    sections.append((section_start, len(body), tuple(text for _, text in headings)))
    intervals = []
    for start, end, path in sections:
        # Blank-line boundaries, except within fenced code. Pipe-table paragraphs stay atomic.
        blocks = []
        block_start = start
        pos = start
        fence = None
        for line in body[start:end].splitlines(keepends=True):
            marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\n"))
            if fence:
                if (
                    marker
                    and marker[1][0] == fence[0]
                    and len(marker[1]) >= fence[1]
                    and not marker[2].strip()
                ):
                    fence = None
            elif marker:
                fence = (marker[1][0], len(marker[1]))
            if not line.strip() and fence is None:
                if body[block_start:pos].strip():
                    blocks.append((block_start, pos + len(line)))
                block_start = pos + len(line)
            pos += len(line)
        if body[block_start:end].strip():
            blocks.append((block_start, end))
        pending = None
        for a, b in blocks:
            text = body[a:b]
            protected = bool(re.search(r"(?m)^ {0,3}(`{3,}|~{3,})", text)) or any(
                "|" in line for line in text.splitlines()
            )
            if b - a > MAX_CHARS:
                if pending:
                    intervals.append((*pending, path))
                    pending = None
                if protected:
                    raise ValueError("oversize_code_or_table")
                cursor = a
                while cursor < b:
                    stop = min(cursor + MAX_CHARS, b)
                    intervals.append((cursor, stop, path))
                    if stop == b:
                        break
                    cursor = stop - OVERLAP
            elif pending and b - pending[0] <= MAX_CHARS:
                pending = (pending[0], b)
            else:
                if pending:
                    intervals.append((*pending, path))
                pending = (a, b)
        if pending:
            intervals.append((*pending, path))
    result = []
    for start, end, path in intervals:
        text = body[start:end]
        if not text.strip():
            continue
        embedding_input = " / ".join(path) + "\n\n" + text
        # Conservative UTF-8 byte bound, not a claimed token count. Batch size is separately capped.
        if len(embedding_input.encode("utf-8")) > 8192:
            raise ValueError("embedding_input_too_large")
        result.append(
            Chunk(
                len(result),
                text,
                start,
                end,
                body.count("\n", 0, start) + 1,
                body.count("\n", 0, max(start, end - 1)) + 1,
                path,
                hashlib.sha256(text.encode()).hexdigest(),
                embedding_input,
                hashlib.sha256(embedding_input.encode()).hexdigest(),
            )
        )
    if not result:
        raise ValueError("no_chunks")
    return result
