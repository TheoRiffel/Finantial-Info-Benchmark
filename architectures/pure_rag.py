"""Pure RAG: hybrid search → rerank → Claude Haiku (no tools)."""
import re
import time

import config
from architectures.base import BaseArchitecture, BenchmarkRun
from shared.search import HybridSearcher, Reranker, SearchResult
from shared import llm


def _build_context(results: list[SearchResult]) -> tuple[str, list[dict]]:
    """Format top chunks as numbered [doc_N] blocks within token budget."""
    parts: list[str] = []
    sources: list[dict] = []
    total_chars = 0
    budget = config.MAX_CONTEXT_TOKENS * 4

    for i, r in enumerate(results, 1):
        date_str = f" (published: {r.date})" if r.date else ""
        block = (
            f"[doc_{i}] Title: {r.doc_title}{date_str}\n"
            f"Section: {r.section_path}\n"
            f"Content:\n{r.raw_text}\n"
        )
        if total_chars + len(block) > budget:
            break
        parts.append(block)
        total_chars += len(block)
        sources.append({"doc_n": i, "doc_id": r.doc_id, "doc_title": r.doc_title, "date": r.date})

    return "\n---\n".join(parts), sources


class PureRAG(BaseArchitecture):
    name = "pure_rag"

    def load(self) -> None:
        self._searcher = HybridSearcher()
        self._reranker = Reranker()

    def unload(self) -> None:
        self._searcher.close()

    def run_query(self, question: str) -> BenchmarkRun:
        t_total = time.perf_counter()

        # ── Retrieval ────────────────────────────────────────────────────────
        t_ret = time.perf_counter()
        candidates = self._searcher.search(question)
        top = self._reranker.rerank(question, candidates)
        retrieval_time = time.perf_counter() - t_ret

        retrieved_ids = [r.chunk_id for r in top]
        context, sources = _build_context(top)

        user_prompt = (
            f"DOCUMENTS:\n{context}\n\n---\n\n"
            f"QUESTION: {question}\n\n"
            f"Answer using [doc_N] citations for every factual claim."
        )

        # ── Generation ───────────────────────────────────────────────────────
        t_gen = time.perf_counter()
        response = llm.generate(user_prompt)
        generation_time = time.perf_counter() - t_gen

        answer = next((b.text for b in response.content if b.type == "text"), "")
        usage = llm.extract_usage(response)
        cost = llm.compute_cost(**usage)

        return BenchmarkRun(
            answer=answer,
            retrieved_ids=retrieved_ids,
            latency={
                "retrieval":   retrieval_time,
                "generation":  generation_time,
                "total":       time.perf_counter() - t_total,
            },
            tokens=usage,
            cost_usd=cost,
            metadata={"sources": sources},
        )
