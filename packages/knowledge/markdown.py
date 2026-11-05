import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MarkdownDocument:
    document_key: str
    title: str
    source_path: str
    body: str
    data_version: str
    product_versions: tuple[str, ...]
    synthetic: bool
    content_hash: str
    revision_hash: str


def load_markdown(path: Path, *, root: Path, product_versions: list[str]) -> MarkdownDocument:
    root, path = root.resolve(strict=True), path.resolve(strict=True)
    if not path.is_relative_to(root) or path.suffix.lower() != ".md":
        raise ValueError("Only Markdown files inside the source root are accepted")
    if path.stat().st_size > 1_000_000:
        raise ValueError("Markdown file exceeds 1 MB")
    raw = path.read_bytes()
    if len(raw) > 1_000_000:
        raise ValueError("Markdown file exceeds 1 MB")
    body = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in body:
        raise ValueError("NUL is not valid document text")

    def field(pattern):
        matches = re.findall(pattern, body, re.MULTILINE)
        if len(matches) != 1:
            raise ValueError("Missing or ambiguous Markdown metadata")
        return matches[0].strip()

    key = field(r"^文档 ID：([a-z0-9-]+)\s*$")
    title = field(r"^# ([^\n]+)$")
    data_version = field(r"^数据版本：([^\n]+)$")
    if key != path.stem or not re.search(r"^Synthetic：true(?:（.*）)?\s*$", body, re.MULTILINE):
        raise ValueError("Document ID/filename or synthetic marker is invalid")
    versions = tuple(sorted(set(product_versions)))
    if not versions or any(v not in {"1.0", "2.0"} for v in versions):
        raise ValueError("Explicit supported product versions are required")
    content_hash = hashlib.sha256(body.encode()).hexdigest()
    source = path.relative_to(root).as_posix()
    # Import metadata participates in revision identity, not only body bytes.
    canonical = json.dumps(
        [content_hash, source, data_version, versions, True],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return MarkdownDocument(
        key,
        title,
        source,
        body,
        data_version,
        versions,
        True,
        content_hash,
        hashlib.sha256(canonical.encode()).hexdigest(),
    )


def load_directory(directory: Path, *, root: Path, product_versions: list[str]):
    root, directory = root.resolve(strict=True), directory.resolve(strict=True)
    if not directory.is_relative_to(root):
        raise ValueError("Source directory must be inside root")
    paths = sorted(directory.glob("*.md"))
    if not paths or len(paths) > 100:
        raise ValueError("Import batch must contain 1 to 100 Markdown files")
    items = [load_markdown(p, root=root, product_versions=product_versions) for p in paths]
    if len({item.document_key for item in items}) != len(items):
        raise ValueError("Duplicate document keys in one batch")
    return items
