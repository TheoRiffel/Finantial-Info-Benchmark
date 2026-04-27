"""LLM-as-judge using tool-use structured output and local disk cache.

Public API:
    judge(question, gold_facts, forbidden_claims, context, answer) -> JudgeResult
"""
import hashlib
import json
from pathlib import Path

import config
from shared.llm import get_client

CACHE_PATH = Path(__file__).parent.parent / "results" / ".judge_cache.json"

# ── Tool schema ───────────────────────────────────────────────────────────────

_TOOL = {
    "name": "record_judgment",
    "description": "Record evaluation scores for the AI-generated answer.",
    "input_schema": {
        "type": "object",
        "properties": {
            "faithfulness": {
                "type": "number",
                "description": (
                    "0.0–1.0: fraction of the answer's factual claims that are "
                    "directly supported by the provided context."
                ),
            },
            "correctness": {
                "type": "number",
                "description": (
                    "0.0–1.0: fraction of gold_facts that are present or clearly "
                    "implied in the answer. 1.0 if gold_facts is empty."
                ),
            },
            "hallucination_count": {
                "type": "integer",
                "description": (
                    "Number of specific factual claims in the answer (numbers, "
                    "names, events) that are NOT found in the context."
                ),
            },
            "refusal_correct": {
                "type": "boolean",
                "description": (
                    "True if the model correctly acknowledged it could not answer "
                    "due to missing information. False if it answered confidently "
                    "when it should have refused."
                ),
            },
            "reasoning": {
                "type": "string",
                "description": "One sentence explaining the most important score.",
            },
        },
        "required": [
            "faithfulness", "correctness", "hallucination_count",
            "refusal_correct", "reasoning",
        ],
    },
}

_SYSTEM = (
    "You are a rigorous evaluator of AI-generated financial Q&A answers. "
    "Score strictly against the provided context and gold facts — do not use "
    "external knowledge. Call record_judgment with calibrated, honest scores."
)


# ── Result type ───────────────────────────────────────────────────────────────

class JudgeResult:
    __slots__ = ("faithfulness", "correctness", "hallucination_count", "refusal_correct", "reasoning")

    def __init__(
        self,
        faithfulness: float,
        correctness: float,
        hallucination_count: int,
        refusal_correct: bool,
        reasoning: str = "",
    ) -> None:
        self.faithfulness = faithfulness
        self.correctness = correctness
        self.hallucination_count = hallucination_count
        self.refusal_correct = refusal_correct
        self.reasoning = reasoning

    def to_dict(self) -> dict:
        return {
            "faithfulness":       self.faithfulness,
            "correctness":        self.correctness,
            "hallucination_count": self.hallucination_count,
            "refusal_correct":    self.refusal_correct,
            "reasoning":          self.reasoning,
        }


_ERROR_RESULT = JudgeResult(0.0, 0.0, 0, False, "judge error")


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _cache_key(question: str, answer: str) -> str:
    return hashlib.sha256(f"{question}\n{answer}".encode()).hexdigest()


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except Exception:
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2))


# ── Public API ────────────────────────────────────────────────────────────────

def judge(
    question: str,
    gold_facts: list[str],
    forbidden_claims: list[str],
    context: str,
    answer: str,
) -> JudgeResult:
    """Score `answer` against `context` and expected facts. Cached by hash."""
    if not answer.strip():
        return JudgeResult(0.0, 0.0, 0, False, "empty answer")

    cache = _load_cache()
    key = _cache_key(question, answer)
    if key in cache:
        return JudgeResult(**cache[key])

    gold_block = (
        "\n".join(f"- {f}" for f in gold_facts)
        if gold_facts
        else "(none — evaluate overall correctness)"
    )
    forbidden_block = (
        "\n".join(f"- {c}" for c in forbidden_claims)
        if forbidden_claims
        else "(none)"
    )

    prompt = (
        f"QUESTION: {question}\n\n"
        f"CONTEXT (retrieved):\n{context[:3000] if context else '(empty)'}\n\n"
        f"GOLD FACTS expected in answer:\n{gold_block}\n\n"
        f"FORBIDDEN CLAIMS (must not appear):\n{forbidden_block}\n\n"
        f"ANSWER TO EVALUATE:\n{answer}\n\n"
        "Call record_judgment with your scores."
    )

    try:
        response = get_client().messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=300,
            system=[{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}],
            tools=[_TOOL],
            tool_choice={"type": "any"},
            messages=[{"role": "user", "content": prompt}],
        )
        tool_input = next(
            (b.input for b in response.content
             if b.type == "tool_use" and b.name == "record_judgment"),
            None,
        )
        if tool_input is None:
            return _ERROR_RESULT

        result = JudgeResult(
            faithfulness=float(tool_input.get("faithfulness", 0.0)),
            correctness=float(tool_input.get("correctness", 0.0)),
            hallucination_count=int(tool_input.get("hallucination_count", 0)),
            refusal_correct=bool(tool_input.get("refusal_correct", False)),
            reasoning=str(tool_input.get("reasoning", "")),
        )
        cache[key] = result.to_dict()
        _save_cache(cache)
        return result

    except Exception as exc:
        return JudgeResult(0.0, 0.0, 0, False, f"judge error: {exc}")
