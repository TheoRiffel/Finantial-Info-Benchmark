"""Pure RAG: hybrid search → rerank → Claude Haiku (no tools)."""
import time

import config
from architectures.base import BaseArchitecture, BenchmarkRun
from shared.search import HybridSearcher, Reranker, SearchResult
from shared import llm


def _build_context(results: list[SearchResult]) -> tuple[str, list[dict]]:
    """Format top chunks as [filename] blocks within token budget."""
    parts: list[str] = []
    sources: list[dict] = []
    total_chars = 0
    budget = config.MAX_CONTEXT_TOKENS * 4

    for r in results:
        date_str = f" (published: {r.date})" if r.date else ""
        block = (
            f"[{r.doc_id}] Title: {r.doc_title}{date_str}\n"
            f"Section: {r.section_path}\n"
            f"Content:\n{r.raw_text}\n"
        )
        if total_chars + len(block) > budget:
            break
        parts.append(block)
        total_chars += len(block)
        sources.append({"doc_id": r.doc_id, "doc_title": r.doc_title, "date": r.date})

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

        # ── Retrieval ─────────────────────────────────────────────────────────
        t_ret = time.perf_counter()
        candidates = self._searcher.search(question)
        top = self._reranker.rerank(question, candidates)
        retrieval_time = time.perf_counter() - t_ret

        chunk_ids = [r.chunk_id for r in top]
        context, sources = _build_context(top)

        user_prompt = (
            f"DOCUMENTS:\n{context}\n\n---\n\n"
            f"QUESTION: {question}\n\n"
            f"Answer using [filename] citations for every factual claim."
        )

        # ── Generation ────────────────────────────────────────────────────────
        t_gen = time.perf_counter()
        response = llm.generate(user_prompt, system=llm.RAG_SYSTEM_PROMPT)
        generation_time = time.perf_counter() - t_gen

        answer = next((b.text for b in response.content if b.type == "text"), "")
        usage = llm.extract_usage(response)

        return BenchmarkRun(
            answer=answer,
            retrieved_ids=chunk_ids,
            accessed_ids=chunk_ids,   # same docs; no tool calls
            latency={
                "retrieval":  retrieval_time,
                "generation": generation_time,
                "total":      time.perf_counter() - t_total,
            },
            tokens=usage,
            cost_usd=llm.compute_cost(**usage),
            metadata={"sources": sources},
        )
