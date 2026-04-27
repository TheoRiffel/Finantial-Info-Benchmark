"""Hybrid: RAG pre-retrieval supplies context, Claude can use tools for more."""
import time

import config
from architectures.base import BaseArchitecture, BenchmarkRun
from shared.search import HybridSearcher, Reranker, SearchResult
from shared.corpus_tools import CorpusIndex, TOOLS
from shared import llm


def _format_preretrieval(results: list[SearchResult]) -> tuple[str, list[dict]]:
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


class Hybrid(BaseArchitecture):
    name = "hybrid"

    def __init__(self):
        self._corpus = CorpusIndex()

    def load(self) -> None:
        self._searcher = HybridSearcher()
        self._reranker = Reranker()
        self._corpus.load()

    def run_query(self, question: str) -> BenchmarkRun:
        t_total = time.perf_counter()
        client = llm.get_client()
        system_blocks = llm.build_cached_system()

        # ── Step 1: RAG retrieval ─────────────────────────────────────────────
        t_ret = time.perf_counter()
        candidates = self._searcher.search(question)
        top = self._reranker.rerank(question, candidates)
        retrieval_time = time.perf_counter() - t_ret

        retrieved_ids = [r.chunk_id for r in top]
        context, sources = _format_preretrieval(top)

        # ── Step 2: Agentic generation with optional tool use ─────────────────
        initial_content = (
            f"I pre-retrieved the following documents for your question.\n\n"
            f"PRE-RETRIEVED DOCUMENTS:\n{context}\n\n---\n\n"
            f"QUESTION: {question}\n\n"
            f"Use the pre-retrieved documents to answer. "
            f"If they are insufficient, use the tools (grep_corpus, read_article) to find more. "
            f"Cite all claims with [doc_N] for pre-retrieved docs."
        )

        messages = [{"role": "user", "content": initial_content}]
        cumulative = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        tool_calls = 0
        accessed_ids: list[str] = []
        answer = ""

        t_gen = time.perf_counter()
        for _ in range(config.AGENTIC_MAX_ITERATIONS):
            response = client.messages.create(
                model=config.ANTHROPIC_MODEL,
                max_tokens=2048,
                system=system_blocks,
                tools=TOOLS,
                messages=messages,
            )
            cumulative = llm.add_usage(cumulative, llm.extract_usage(response))

            if response.stop_reason == "end_turn":
                answer = next((b.text for b in response.content if b.type == "text"), "")
                break

            if response.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": response.content})
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        tool_calls += 1
                        result = self._corpus.run_tool(block.name, block.input)
                        if block.name == "read_article":
                            fname = block.input.get("filename", "")
                            if fname and fname not in accessed_ids:
                                accessed_ids.append(fname)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })
                messages.append({"role": "user", "content": tool_results})
            else:
                answer = next((b.text for b in response.content if b.type == "text"), "")
                break
        else:
            answer = next((b.text for b in response.content if b.type == "text"), "")

        generation_time = time.perf_counter() - t_gen
        return BenchmarkRun(
            answer=answer,
            retrieved_ids=retrieved_ids,
            accessed_ids=accessed_ids,
            latency={
                "retrieval":  retrieval_time,
                "generation": generation_time,
                "total":      time.perf_counter() - t_total,
            },
            tokens=cumulative,
            cost_usd=llm.compute_cost(**cumulative),
            tool_calls=tool_calls,
            metadata={"sources": sources},
        )
