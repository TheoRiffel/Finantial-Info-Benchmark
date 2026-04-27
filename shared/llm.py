"""Anthropic API client wrapper with token tracking, cost computation, and prompt caching."""
import anthropic

import config

# Singleton client
_client: anthropic.Anthropic | None = None

SYSTEM_PROMPT = """You are a financial research assistant. Answer questions based ONLY on the documents provided to you.

Rules:
1. Use ONLY information from the provided documents — never external knowledge.
2. Cite every factual claim with [doc_N] inline (where N matches the document number).
3. If documents disagree, present both views with their citations.
4. Always include the publication date when stating numbers or specific events.
5. If the documents lack sufficient information, say so explicitly.
6. End every response with: "Note: This is informational synthesis, not investment advice."
"""


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def compute_cost(
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    return (
        input_tokens       * config.COST_INPUT_PER_M       / 1_000_000
        + output_tokens    * config.COST_OUTPUT_PER_M      / 1_000_000
        + cache_read_tokens  * config.COST_CACHE_READ_PER_M  / 1_000_000
        + cache_write_tokens * config.COST_CACHE_WRITE_PER_M / 1_000_000
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
