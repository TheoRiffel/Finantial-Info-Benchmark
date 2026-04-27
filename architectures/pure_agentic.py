"""Pure Agentic: Claude Haiku with grep_corpus + read_article tools, no pre-retrieval."""
import time

import config
from architectures.base import BaseArchitecture, BenchmarkRun
from shared.corpus_tools import CorpusIndex, TOOLS
from shared import llm


class PureAgentic(BaseArchitecture):
    name = "pure_agentic"

    def __init__(self):
        self._corpus = CorpusIndex()

    def load(self) -> None:
        self._corpus.load()

    def run_query(self, question: str) -> BenchmarkRun:
        t_total = time.perf_counter()
        client = llm.get_client()
        system_blocks = llm.build_cached_system()

        messages = [{"role": "user", "content": question}]
        cumulative = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        tool_calls = 0
        accessed_ids: list[str] = []
        answer = ""

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
                        # Track which articles were actually read
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
                # Unexpected stop reason — extract any text and exit
                answer = next((b.text for b in response.content if b.type == "text"), "")
                break
        else:
            answer = next((b.text for b in response.content if b.type == "text"), "")
            answer = f"[max iterations reached] {answer}"

        total_time = time.perf_counter() - t_total
        return BenchmarkRun(
            answer=answer,
            accessed_ids=accessed_ids,
            latency={"retrieval": 0.0, "generation": total_time, "total": total_time},
            tokens=cumulative,
            cost_usd=llm.compute_cost(**cumulative),
            tool_calls=tool_calls,
        )
