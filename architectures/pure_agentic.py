"""Pure Agentic: Claude Haiku with corpus tools, no pre-retrieval."""
import re
import time

import config
from architectures.base import BaseArchitecture, BenchmarkRun
from shared.corpus_tools import CorpusIndex, TOOLS_AGENTIC
from shared import llm

_CITED_RE = re.compile(r"\[([^\]]+\.md)\]")


class PureAgentic(BaseArchitecture):
    name = "pure_agentic"

    def __init__(self) -> None:
        self._corpus = CorpusIndex()

    def load(self) -> None:
        self._corpus.load()

    def run_query(self, question: str) -> BenchmarkRun:
        t_total = time.perf_counter()
        client = llm.get_client()
        system_blocks = llm.build_cached_system(llm.AGENTIC_SYSTEM_PROMPT)

        messages = [{"role": "user", "content": question}]
        cumulative = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        tool_calls = 0
        accessed_ids: list[str] = []
        answer = ""

        for _ in range(config.AGENTIC_MAX_ITERATIONS):
            if tool_calls >= config.AGENTIC_MAX_TOOL_CALLS:
                answer = f"[tool call limit reached] {answer}"
                break

            response = client.messages.create(
                model=config.ANTHROPIC_MODEL,
                max_tokens=2048,
                system=system_blocks,
                tools=TOOLS_AGENTIC,
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
            answer = f"[max iterations reached] {answer}"

        # Extract [filename.md] citations from the final answer
        retrieved_ids = list(dict.fromkeys(_CITED_RE.findall(answer)))

        total_time = time.perf_counter() - t_total
        return BenchmarkRun(
            answer=answer,
            retrieved_ids=retrieved_ids,
            accessed_ids=accessed_ids,
            latency={"retrieval": 0.0, "generation": total_time, "total": total_time},
            tokens=cumulative,
            cost_usd=llm.compute_cost(**cumulative),
            tool_calls=tool_calls,
        )
