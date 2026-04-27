"""Structural markdown chunker — ported from RAG_finances/ingestion/chunker.py."""
import re
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterator

import config


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    text: str
    section_path: str
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Front-matter ────────────────────────────────────────────────────────────

_FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def parse_front_matter(content: str) -> tuple[dict, str]:
    m = _FM_RE.match(content)
    if not m:
        return {}, content
    fm_text = m.group(1)
    rest = content[m.end():]
    meta: dict = {}
    for line in fm_text.split("\n"):
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            value = [v.strip() for v in inner.split(",") if v.strip()] if inner else []
        meta[key] = value
    return meta, rest


# ── Token estimation ─────────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


# ── Heading split ────────────────────────────────────────────────────────────

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def split_by_headings(text: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [("(no title)", text.strip())]
    stack: list[tuple[int, str]] = []
    for i, m in enumerate(matches):
        level = len(m.group(1))
        title = m.group(2).strip()
        stack = [(lv, ti) for lv, ti in stack if lv < level]
        stack.append((level, title))
        path = " > ".join(t for _, t in stack)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if body:
            sections.append((path, body))
    return sections


# ── Size split ───────────────────────────────────────────────────────────────

def split_by_size(text: str, target_tokens: int, overlap_tokens: int) -> list[str]:
    if estimate_tokens(text) <= target_tokens:
        return [text]
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    target_chars = target_tokens * 4
    overlap_chars = overlap_tokens * 4
    for para in paragraphs:
        para_tokens = estimate_tokens(para)
        if para_tokens > target_tokens * 2:
            if current:
                chunks.append("\n\n".join(current))
                current, current_tokens = [], 0
            for i in range(0, len(para), target_chars):
                chunks.append(para[i: i + target_chars])
            continue
        if current_tokens + para_tokens > target_tokens and current:
            chunks.append("\n\n".join(current))
            tail = current[-1] if current else ""
            if estimate_tokens(tail) * 4 <= overlap_chars:
                current = [tail, para]
                current_tokens = estimate_tokens(tail) + para_tokens
            else:
                current, current_tokens = [para], para_tokens
        else:
            current.append(para)
            current_tokens += para_tokens
    if current:
        chunks.append("\n\n".join(current))
    return chunks


# ── Public API ───────────────────────────────────────────────────────────────

def chunk_document(filepath: Path) -> list[Chunk]:
    content = filepath.read_text(encoding="utf-8")
    metadata, body = parse_front_matter(content)
    doc_id = filepath.name
    sections = split_by_headings(body)
    chunks: list[Chunk] = []
    for section_path, section_text in sections:
        for sub_text in split_by_size(
            section_text,
            target_tokens=config.CHUNK_SIZE_TOKENS,
            overlap_tokens=config.CHUNK_OVERLAP_TOKENS,
        ):
            if estimate_tokens(sub_text) < config.MIN_CHUNK_TOKENS:
                continue
            doc_title = metadata.get("title", filepath.stem)
            embedding_text = (
                f"[Document: {doc_title}]\n"
                f"[Section: {section_path}]\n\n"
                + sub_text
            )
            chunks.append(Chunk(
                chunk_id=str(uuid.uuid4()),
                doc_id=doc_id,
                text=embedding_text,
                section_path=section_path,
                metadata={**metadata, "raw_text": sub_text, "doc_title": doc_title},
            ))
    return chunks


def chunk_corpus(corpus_dir: Path | None = None) -> Iterator[Chunk]:
    corpus_dir = corpus_dir or config.CORPUS_DIR
    for fp in sorted(corpus_dir.glob("*.md")):
        yield from chunk_document(fp)
