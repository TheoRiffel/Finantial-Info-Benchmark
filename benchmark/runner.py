"""Benchmark runner: iterates architectures × queries, collects metrics, saves JSON."""
import json
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import config
from benchmark.judge import judge_answer

if TYPE_CHECKING:
    from architectures.base import BaseArchitecture

EVAL_PATH = Path(__file__).parent / "eval_set.json"


# ── Heuristic metrics ────────────────────────────────────────────────────────

def _topic_coverage(text: str, topics: list[str]) -> float:
    if not topics:
        return 1.0
    t = text.lower()
    return sum(1 for topic in topics if topic.lower() in t) / len(topics)


def _ticker_recall(text: str, tickers: list[str]) -> float:
    if not tickers:
        return 1.0
    t = text.upper()
    return sum(1 for ticker in tickers if ticker.upper() in t) / len(tickers)


# ── Main runner ──────────────────────────────────────────────────────────────

def run_benchmark(
    architectures: "list[BaseArchitecture]",
    use_judge: bool = True,
) -> dict:
    with open(EVAL_PATH) as f:
        queries = json.load(f)["queries"]

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    results: dict = {
        "run_id": run_id,
        "metadata": {
            "timestamp":  datetime.now().isoformat(),
            "model":      config.ANTHROPIC_MODEL,
            "n_queries":  len(queries),
        },
        "architectures": {},
    }

    for arch in architectures:
        print(f"\n{'='*60}")
        print(f"  Architecture: {arch.name.upper()}")
        print(f"{'='*60}")
        print("  Loading models/index...")
        arch.load()

        arch_rows: list[dict] = []
        for q in queries:
            print(f"\n  [{q['id']}] {q['question'][:72]}")
            try:
                run = arch.run_query(q["question"])
                row = asdict(run)
            except Exception as exc:
                print(f"    ERROR: {exc}")
                row = {
                    "answer": "", "retrieved_ids": [], "accessed_ids": [],
                    "latency": {}, "tokens": {}, "cost_usd": 0.0,
                    "tool_calls": 0, "error": str(exc), "metadata": {},
                }

            # Attach query context
            row["query_id"] = q["id"]
            row["question"] = q["question"]

            # Heuristic metrics
            ans = row.get("answer", "")
            row["topic_coverage"] = _topic_coverage(ans, q.get("expected_topics", []))
            row["ticker_recall"]  = _ticker_recall(ans, q.get("expected_tickers", []))
            row["citation_count"] = ans.count("[doc_")

            # LLM judge (optional)
            if use_judge and ans and not row.get("error"):
                row["judge_scores"] = judge_answer(q["question"], ans)
            else:
                row["judge_scores"] = {}

            lat = row.get("latency", {})
            tok = row.get("tokens", {})
            print(
                f"    total={lat.get('total', 0):.1f}s  "
                f"tokens={tok.get('input', 0)}+{tok.get('output', 0)}  "
                f"cache_read={tok.get('cache_read', 0)}  "
                f"tools={row.get('tool_calls', 0)}  "
                f"cost=${row.get('cost_usd', 0):.4f}"
            )
            arch_rows.append(row)

        results["architectures"][arch.name] = arch_rows

    return results


def save_results(results: dict) -> Path:
    path = config.ROOT / "results" / f"run_{results['run_id']}.json"
    with open(path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  Results saved → {path}")
    return path
