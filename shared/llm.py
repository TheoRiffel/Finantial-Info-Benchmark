"""Anthropic API client wrapper with token tracking, cost computation, and prompt caching."""
import anthropic

import config

# Singleton client
_client: anthropic.Anthropic | None = None

RAG_SYSTEM_PROMPT = """You are a financial research assistant. Answer questions using ONLY the documents provided below.

Rules:
1. Cite every factual claim with the document filename in brackets, e.g. [00042_some-title.md]. The filename is shown at the start of each document block.
2. Clearly distinguish between factual statements (from the document) and analysis or opinion.
3. If documents disagree, present both views with their citations.
4. Always include publication dates when citing specific numbers or events.
5. If the provided documents lack sufficient information, say so explicitly — do not speculate.
6. End every response with: "Note: This is informational synthesis, not investment advice."
"""

AGENTIC_SYSTEM_PROMPT = """You are a financial research assistant with tools to search a corpus of financial news documents.

Playbook:
1. Start with list_documents or search_index to identify relevant documents.
2. Use grep for specific terms, tickers, or numeric values.
3. Use read_section for targeted reading; use read_document with line_range only for long documents.
4. For broad queries, stop once you have 3+ independent sources confirming the key facts.
5. Hard limit: 10 tool calls. Stop and answer with what you have found.

Citation rules:
- Cite every factual claim with the document filename in brackets, e.g. [00042_some-title.md].
- Clearly distinguish fact (stated in the document) from opinion or inference.
- If information is insufficient, say so explicitly — do not speculate.
- End every response with: "Note: This is informational synthesis, not investment advice."
"""

HYBRID_SYSTEM_PROMPT = """You are a financial research assistant. Pre-retrieved documents are provided; you also have tools to find more.

Strategy:
- Check the pre-retrieved documents first — they often contain the answer.
- Specific queries (tickers, exact figures, named events): use grep for precision.
- Broad semantic queries (themes, comparisons, trends): use hybrid_search.
- When precision matters, use both and cross-validate the results.

Citation rules:
- Cite every factual claim with the document filename in brackets, e.g. [00042_some-title.md]. The filename is shown at the start of each pre-retrieved document block.
- Clearly distinguish fact from opinion.
- End every response with: "Note: This is informational synthesis, not investment advice."
"""

SYSTEM_PROMPT = RAG_SYSTEM_PROMPT  # default used by llm.generate()


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def compute_cost(
    input: int = 0,
    output: int = 0,
    cache_read: int = 0,
    cache_write: int = 0,
) -> float:
    return (
        input       * config.COST_INPUT_PER_M       / 1_000_000
        + output    * config.COST_OUTPUT_PER_M      / 1_000_000
        + cache_read  * config.COST_CACHE_READ_PER_M  / 1_000_000
        + cache_write * config.COST_CACHE_WRITE_PER_M / 1_000_000
    )


def build_cached_system(system: str | None = None) -> list[dict]:
    """Return system prompt as a list with cache_control on the text block."""
    return [{"type": "text", "text": system or SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]


def generate(
    user_prompt: str,
    system: str | None = None,
    max_tokens: int = 2048,
) -> anthropic.types.Message:
    """Single-turn generation with cached system prompt."""
    return get_client().messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=max_tokens,
        system=build_cached_system(system),
        messages=[{"role": "user", "content": user_prompt}],
    )


def extract_usage(response: anthropic.types.Message) -> dict:
    u = response.usage
    return {
        "input":       u.input_tokens,
        "output":      u.output_tokens,
        "cache_read":  getattr(u, "cache_read_input_tokens",  0) or 0,
        "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0,
    }


def add_usage(a: dict, b: dict) -> dict:
    return {k: a.get(k, 0) + b.get(k, 0) for k in ("input", "output", "cache_read", "cache_write")}
