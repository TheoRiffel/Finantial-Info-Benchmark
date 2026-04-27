"""Benchmark runner: runs one architecture over an eval set, saves JSONL.

Public API:
    run_benchmark(arch, queries, use_judge=True) -> list[dict]
    save_jsonl(rows, arch_name) -> Path
"""
import json
import pickle
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import config
from benchmark.judge import judge

if TYPE_CHECKING:
    from architectures.base import BaseArchitecture

RUNS_DIR = config.ROOT / "results" / "runs"
RUNS_DIR.mkdir(parents=True, exist_ok=True)


# ── Context reconstruction ────────────────────────────────────────────────────

def _load_chunks() -> dict[str, dict]:
    path = config.INDEX_DIR / "chunks.pkl"
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return {c["chunk_id"]: c for c in pickle.load(f)}


def _build_context(row: dict, chunks_by_id: dict[str, dict]) -> str:
    ids = (row.get("retrieved_ids") or []) + (row.get("accessed_ids") or [])
    parts: list[str] = []
    for cid in ids[:10]:
        c = chunks_by_id.get(cid)
        if c:
            parts.append(f"[{c['doc_title']}]\n{c['raw_text'][:600]}")
    return "\n\n---\n\n".join(parts)


# ── Runner ────────────────────────────────────────────────────────────────────

def run_benchmark(
    arch: "BaseArchitecture",
    queries: list[dict],
    use_judge: bool = True,
) -> list[dict]:
    """Run `arch` over `queries`. Returns one row dict per query."""
    chunks_by_id = _load_chunks()
    rows: list[dict] = []

    for q in queries:
        print(f"\n  [{q['id']}] [{q.get('category', '?')}] {q['question'][:65]}")
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

        row.update({
            "query_id":         q["id"],
            "question":         q["question"],
            "category":         q.get("category", ""),
            "gold_chunk_ids":   q.get("gold_chunk_ids", []),
            "gold_facts":       q.get("gold_facts", []),
            "forbidden_claims": q.get("forbidden_claims", []),
        })

        if use_judge and row.get("answer") and not row.get("error"):
            context = _build_context(row, chunks_by_id)
            verdict = judge(
                question=q["question"],
                gold_facts=q.get("gold_facts", []),
                forbidden_claims=q.get("forbidden_claims", []),
                context=context,
                answer=row["answer"],
            )
            row["judge"] = verdict.to_dict()
        else:
            row["judge"] = {}

        _print_row(row)
        rows.append(row)

    return rows


def _print_row(row: dict) -> None:
    lat = row.get("latency", {})
    tok = row.get("tokens", {})
    j   = row.get("judge", {})
    parts = [f"    {lat.get('total', 0):.1f}s"]
    if j:
        parts += [
            f"faith={j.get('faithfulness', 0):.2f}",
            f"corr={j.get('correctness', 0):.2f}",
            f"hall={j.get('hallucination_count', 0)}",
        ]
    else:
        parts.append(f"tokens={tok.get('input', 0)}+{tok.get('output', 0)}")
    parts.append(f"cost=${row.get('cost_usd', 0):.4f}")
    print("  ".join(parts))


# ── Persistence ───────────────────────────────────────────────────────────────

def save_jsonl(rows: list[dict], arch_name: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RUNS_DIR / f"{arch_name}_{ts}.jsonl"
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row, default=str) + "\n")
    print(f"  Saved → {path}")
    return path
