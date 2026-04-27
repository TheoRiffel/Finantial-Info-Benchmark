"""In-memory corpus index + tool definitions for agentic architectures."""
from pathlib import Path

import config

# ── Tool schemas (Anthropic tool_use format) ─────────────────────────────────

TOOLS = [
    {
        "name": "grep_corpus",
        "description": (
            "Search the financial news corpus by keyword or phrase. "
            "Returns up to max_results matching article filenames with their titles and dates."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Case-insensitive keyword or phrase to search for in article titles and content.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default: 15, max: 30).",
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "read_article",
        "description": "Read the full content of a specific financial news article from the corpus.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Filename of the article to read (e.g., '0042.md').",
                }
            },
            "required": ["filename"],
        },
    },
]


# ── Corpus index ─────────────────────────────────────────────────────────────

class CorpusIndex:
    """Loads corpus markdown files into memory for fast grep/read."""

    def __init__(self):
        self._index: dict[str, dict] = {}  # filename → {title, date, content}

    def load(self, corpus_dir: Path | None = None) -> None:
        corpus_dir = corpus_dir or config.CORPUS_DIR
        if not corpus_dir.exists():
            raise FileNotFoundError(
                f"Corpus directory not found: {corpus_dir}\n"
                "Run 'python -m ingestion.download_data' from RAG_finances first."
            )
        for fp in sorted(corpus_dir.glob("*.md")):
            content = fp.read_text(encoding="utf-8")
            title, date = fp.stem, ""
            if content.startswith("---"):
                for line in content[3:].split("\n"):
                    if line.startswith("title:"):
                        title = line.partition(":")[2].strip().strip('"')
                    elif line.startswith("date:"):
                        date = line.partition(":")[2].strip()
                    elif line == "---":
                        break
            self._index[fp.name] = {"title": title, "date": date, "content": content}

    # ── Tool implementations ─────────────────────────────────────────────────

    def grep_corpus(self, pattern: str, max_results: int = 15) -> str:
        max_results = min(max_results, 30)
        pattern_lower = pattern.lower()
        matches: list[str] = []
        for filename, data in self._index.items():
            if pattern_lower in data["title"].lower() or pattern_lower in data["content"].lower():
                matches.append(f"{filename} | {data['title']} | {data['date']}")
        if not matches:
            return f"No articles found matching '{pattern}'."
        result = "\n".join(matches[:max_results])
        if len(matches) > max_results:
            result += f"\n... ({len(matches) - max_results} more — refine your search)"
        return result

    def read_article(self, filename: str) -> str:
        data = self._index.get(filename)
        if not data:
            available = list(self._index.keys())[:5]
            return (
                f"Article '{filename}' not found. "
                f"Example valid filenames: {available}. "
                "Use grep_corpus to search for articles."
            )
        content = data["content"]
        # Cap at 4000 chars to keep context manageable
        if len(content) > 4000:
            content = content[:4000] + "\n[... content truncated ...]"
        return content

    def run_tool(self, name: str, inputs: dict) -> str:
        if name == "grep_corpus":
            return self.grep_corpus(
                inputs["pattern"],
                max_results=inputs.get("max_results", 15),
            )
        if name == "read_article":
            return self.read_article(inputs["filename"])
        return f"Unknown tool: '{name}'"
