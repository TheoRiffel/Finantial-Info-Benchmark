"""In-memory corpus index + tool definitions for agentic architectures."""
import re
from pathlib import Path

import config

# ── Tool schemas ──────────────────────────────────────────────────────────────

TOOLS_AGENTIC = [
    {
        "name": "grep",
        "description": (
            "Search the financial corpus line-by-line for a keyword, phrase, or ticker. "
            "Returns matching lines with doc_id, line number, and surrounding context."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Case-insensitive keyword, phrase, or ticker to search for.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum matching lines to return (default 20, max 50).",
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "read_document",
        "description": (
            "Read a document from the corpus. "
            "If the document exceeds 200 lines, line_range is required."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "doc_id": {
                    "type": "string",
                    "description": "Document filename, e.g. '0042.md'.",
                },
                "line_range": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 2,
                    "maxItems": 2,
                    "description": "[start_line, end_line] (1-indexed, inclusive). Required for documents > 200 lines.",
                },
            },
            "required": ["doc_id"],
        },
    },
    {
        "name": "read_section",
        "description": "Read a specific section of a document by heading text (case-insensitive substring match).",
        "input_schema": {
            "type": "object",
            "properties": {
                "doc_id": {
                    "type": "string",
                    "description": "Document filename, e.g. '0042.md'.",
                },
                "heading": {
                    "type": "string",
                    "description": "Heading text to locate (case-insensitive).",
                },
            },
            "required": ["doc_id", "heading"],
        },
    },
    {
        "name": "list_documents",
        "description": (
            "List corpus documents, optionally filtered by date or ticker symbol. "
            "Returns filename, title, and date for each match."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date_after": {
                    "type": "string",
                    "description": "ISO date (YYYY-MM-DD). Only include documents published on or after this date.",
                },
                "ticker": {
                    "type": "string",
                    "description": "Ticker symbol (e.g. 'AAPL'). Only include documents with this ticker in their metadata.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "search_index",
        "description": (
            "Search the pre-built document index for one-line summaries matching keywords. "
            "Faster than grep — use to identify relevant documents before reading them. "
            "Requires INDEX.md to exist (run scripts/build_index.py first)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "string",
                    "description": "Space-separated keywords to match against the index (all must match).",
                },
            },
            "required": ["keywords"],
        },
    },
]

VECTOR_SEARCH_SCHEMA = {
    "name": "vector_search",
    "description": (
        "Semantic vector search over document chunks. "
        "Best for broad, thematic, or conceptual queries."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Natural-language query to embed and search."},
            "top_k": {"type": "integer", "description": "Number of results to return (default 10)."},
        },
        "required": ["query"],
    },
}

HYBRID_SEARCH_SCHEMA = {
    "name": "hybrid_search",
    "description": (
        "Full hybrid RAG search (dense + BM25 + RRF + cross-encoder rerank). "
        "Best for queries mixing semantic intent and specific keywords."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Natural-language query."},
            "top_k": {"type": "integer", "description": "Number of results after reranking (default 10)."},
        },
        "required": ["query"],
    },
}


# ── Front-matter parser ───────────────────────────────────────────────────────

_FM_RE = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)
_HEADING_RE = re.compile(r"^#{1,6}\s", re.MULTILINE)


def _parse_front_matter(content: str) -> dict:
    m = _FM_RE.match(content)
    if not m:
        return {}
    meta: dict = {}
    for line in m.group(1).split("\n"):
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip().strip('"')
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            value = [v.strip().strip('"') for v in inner.split(",") if v.strip()] if inner else []
        meta[key] = value
    return meta


# ── Corpus index ──────────────────────────────────────────────────────────────

class CorpusIndex:
    """Loads corpus markdown files into memory; dispatches tool calls."""

    def __init__(self) -> None:
        self._docs: dict[str, dict] = {}  # filename → {title, date, tickers, content, lines}

    def load(self, corpus_dir: Path | None = None) -> None:
        corpus_dir = corpus_dir or config.CORPUS_DIR
        if not corpus_dir.exists():
            raise FileNotFoundError(
                f"Corpus directory not found: {corpus_dir}\n"
                "Run 'python -m ingestion.download_data' from RAG_finances first."
            )
        for fp in sorted(corpus_dir.glob("*.md")):
            content = fp.read_text(encoding="utf-8")
            meta = _parse_front_matter(content)
            self._docs[fp.name] = {
                "title":   meta.get("title", fp.stem),
                "date":    meta.get("date", ""),
                "tickers": meta.get("tickers", []),
                "content": content,
                "lines":   content.split("\n"),
            }

    # ── Tool implementations ──────────────────────────────────────────────────

    def grep(self, pattern: str, max_results: int = 20) -> str:
        max_results = min(max_results, 50)
        pattern_lower = pattern.lower()
        matches: list[str] = []
        for doc_id, data in self._docs.items():
            lines = data["lines"]
            for line_num, line in enumerate(lines, 1):
                if pattern_lower in line.lower():
                    ctx_start = max(0, line_num - 3)
                    ctx_end = min(len(lines), line_num + 2)
                    context = "\n".join(lines[ctx_start:ctx_end])
                    matches.append(f"doc_id: {doc_id} | line: {line_num}\n{context}")
                    if len(matches) >= max_results:
                        break
            if len(matches) >= max_results:
                break
        if not matches:
            return f"No matches found for '{pattern}'."
        suffix = (
            f"\n\n[Results capped at {max_results}. Refine your search for more precision.]"
            if len(matches) == max_results else ""
        )
        return "\n\n---\n\n".join(matches) + suffix

    def read_document(self, doc_id: str, line_range: list[int] | None = None) -> str:
        data = self._docs.get(doc_id)
        if not data:
            sample = list(self._docs.keys())[:5]
            return f"Document '{doc_id}' not found. Example doc_ids: {sample}"
        lines = data["lines"]
        total = len(lines)
        if total > 200 and line_range is None:
            return (
                f"Document '{doc_id}' has {total} lines. "
                "Provide line_range=[start, end] to read a specific portion."
            )
        if line_range:
            start, end = max(0, line_range[0] - 1), line_range[1]
            return "\n".join(lines[start:end])
        return data["content"]

    def read_section(self, doc_id: str, heading: str) -> str:
        data = self._docs.get(doc_id)
        if not data:
            return f"Document '{doc_id}' not found."
        heading_lower = heading.lower()
        in_section = False
        section_lines: list[str] = []
        for line in data["lines"]:
            if _HEADING_RE.match(line):
                if in_section:
                    break
                if heading_lower in line.lower():
                    in_section = True
            if in_section:
                section_lines.append(line)
        if not section_lines:
            return f"Section '{heading}' not found in '{doc_id}'."
        return "\n".join(section_lines)

    def list_documents(
        self,
        date_after: str | None = None,
        ticker: str | None = None,
    ) -> str:
        ticker_upper = ticker.upper() if ticker else None
        results: list[str] = []
        for filename, data in self._docs.items():
            if date_after and data["date"] < date_after:
                continue
            if ticker_upper:
                doc_tickers = [t.upper() for t in (data["tickers"] or [])]
                if ticker_upper not in doc_tickers:
                    continue
            results.append(f"{filename} | {data['title']} | {data['date']}")
        if not results:
            return "No documents found matching the criteria."
        lines = sorted(results)
        suffix = f"\n[{len(lines)} documents total]"
        return "\n".join(lines) + suffix

    def search_index(self, keywords: str) -> str:
        index_path = config.INDEX_DIR / "INDEX.md"
        if not index_path.exists():
            return "INDEX.md not found. Run 'python scripts/build_index.py' first."
        kws = [k.strip().lower() for k in keywords.split() if k.strip()]
        matches = [
            line for line in index_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and all(k in line.lower() for k in kws)
        ]
        if not matches:
            return f"No index entries found for: {keywords}"
        return "\n".join(matches)

    def run_tool(self, name: str, inputs: dict) -> str:
        match name:
            case "grep":
                return self.grep(inputs["pattern"], inputs.get("max_results", 20))
            case "read_document":
                return self.read_document(inputs["doc_id"], inputs.get("line_range"))
            case "read_section":
                return self.read_section(inputs["doc_id"], inputs["heading"])
            case "list_documents":
                return self.list_documents(inputs.get("date_after"), inputs.get("ticker"))
            case "search_index":
                return self.search_index(inputs["keywords"])
            case _:
                return f"Unknown tool: '{name}'"
