"""LLM-as-judge: scores answers on 4 criteria using Claude Haiku."""
import json
import re

import config
from shared import llm

_JUDGE_SYSTEM = """You are an objective evaluator of AI-generated financial Q&A answers.

Score the answer on these 4 criteria, each from 1 (poor) to 5 (excellent):
- completeness: does it address all aspects of the question?
- groundedness: are all claims backed by cited sources, with no hallucination?
- citation_quality: are [doc_N] citations present and meaningful?
- conciseness: is the answer focused and appropriately brief?

Respond ONLY with valid JSON (no markdown fences):
{"completeness": N, "groundedness": N, "citation_quality": N, "conciseness": N, "reasoning": "one sentence"}
"""

_EMPTY = {"completeness": 0, "groundedness": 0, "citation_quality": 0, "conciseness": 0, "reasoning": ""}


def judge_answer(question: str, answer: str) -> dict:
    if not answer.strip():
        return {**_EMPTY, "reasoning": "empty answer"}

    prompt = f"QUESTION: {question}\n\nANSWER:\n{answer}"
    try:
        response = llm.get_client().messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=256,
            system=_JUDGE_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in response.content if b.type == "text"), "")
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as e:
        return {**_EMPTY, "reasoning": f"judge error: {e}"}
    return {**_EMPTY, "reasoning": "parse error"}
