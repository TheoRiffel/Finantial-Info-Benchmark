"""Hybrid: RAG pre-retrieval + agentic tools including vector_search and hybrid_search."""
import time

import config
from architectures.base import BaseArchitecture, BenchmarkRun
from shared.search import HybridSearcher, Reranker, SearchResult
from shared.corpus_tools import CorpusIndex, TOOLS_AGENTIC, VECTOR_SEARCH_SCHEMA, HYBRID_SEARCH_SCHEMA
from shared import llm

TOOLS_HYBRID = TOOLS_AGENTIC + [VECTOR_SEARCH_SCHEMA, HYBRID_SEARCH_SCHEMA]


def _format_search_results(results: list[SearchResult]) -> str:
    """Format SearchResults as readable text for tool output."""
    parts = []
    for r in results:
        date_str = f" (published: {r.date})" if r.date else ""
        parts.append(
            f"doc_id: {r.doc_id}{date_str}\n"
            f"Title: {r.doc_title}\n"
            f"Section: {r.section_path}\n"
            f"{r.raw_text}"
        )
    return "\n\n---\n\n".join(parts) if parts else "No results found."


def _format_preretrieval(results: list[SearchResult]) -> tuple[str, list[dict]]:
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


class Hybrid(BaseArchitecture):
    name = "hybrid"

    def __init__(self) -> None:
        self._corpus = CorpusIndex()

    def load(self) -> None:
        self._searcher = HybridSearcher()
        self._reranker = Reranker()
        self._corpus.load()

    def unload(self) -> None:
        self._searcher.close()

    # ── Search tool implementations ───────────────────────────────────────────

    def _vector_search(self, query: str, top_k: int = 10) -> str:
        dense = self._searcher.dense_search(query, top_k)
        results = []
        for cid, _ in dense:
            chunk = self._searcher.chunks_by_id.get(cid)
            if chunk:
                results.append(SearchResult(
                    chunk_id=cid,
                    doc_id=chunk["doc_id"],
                    doc_title=chunk.get("doc_title", ""),
                    section_path=chunk.get("section_path", ""),
                    raw_text=chunk.get("raw_text", ""),
                    date=chunk.get("date", ""),
                    tickers=chunk.get("tickers", []),
                    score=0.0,
                ))
        return _format_search_results(results)

    def _hybrid_search(self, query: str, top_k: int = 10) -> str:
        candidates = self._searcher.search(query)
        top = self._reranker.rerank(query, candidates, top_k=top_k)
        return _format_search_results(top)

    def _run_tool(self, name: str, inputs: dict) -> str:
        if name == "vector_search":
            return self._vector_search(inputs["query"], inputs.get("top_k", 10))
        if name == "hybrid_search":
            return self._hybrid_search(inputs["query"], inputs.get("top_k", 10))
        return self._corpus.run_tool(name, inputs)

    # ── Main query loop ───────────────────────────────────────────────────────

    def run_query(self, question: str) -> BenchmarkRun:
        t_total = time.perf_counter()
        client = llm.get_client()
        system_blocks = llm.build_cached_system(llm.HYBRID_SYSTEM_PROMPT)

        # ── Step 1: RAG pre-retrieval ─────────────────────────────────────────
        t_ret = time.perf_counter()
        candidates = self._searcher.search(question)
        top = self._reranker.rerank(question, candidates)
        retrieval_time = time.perf_counter() - t_ret

        retrieved_ids = [r.chunk_id for r in top]
        context, sources = _format_preretrieval(top)

        initial_content = (
            f"PRE-RETRIEVED DOCUMENTS:\n{context}\n\n---\n\n"
            f"QUESTION: {question}"
        )

        # ── Step 2: Agentic generation ────────────────────────────────────────
        messages = [{"role": "user", "content": initial_content}]
        cumulative = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        tool_calls = 0
        accessed_ids: list[str] = []
        answer = ""

        t_gen = time.perf_counter()
        for _ in range(config.AGENTIC_MAX_ITERATIONS):
            if tool_calls >= config.AGENTIC_MAX_TOOL_CALLS:
                break

            response = client.messages.create(
                model=config.ANTHROPIC_MODEL,
                max_tokens=2048,
                system=system_blocks,
                tools=TOOLS_HYBRID,
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
                        result = self._run_tool(block.name, block.input)
                        if block.name in ("read_document", "read_section"):
                            doc_id = block.input.get("doc_id", "")
                            if doc_id and doc_id not in accessed_ids:
                                accessed_ids.append(doc_id)
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
