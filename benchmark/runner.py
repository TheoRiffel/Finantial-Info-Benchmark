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

from rich.console import Console
from rich.text import Text

import config
from benchmark.judge import judge

if TYPE_CHECKING:
    from architectures.base import BaseArchitecture

console = Console()

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
        qid = q["id"]
        cat = q.get("category", "?")
        question = q["question"][:65]
        console.print(f"  [dim]{qid}[/dim]  [bold cyan]{cat}[/bold cyan]  {question}")

        try:
            run = arch.run_query(q["question"])
            row = asdict(run)
        except Exception as exc:
            console.print(f"    [bold red]ERROR[/bold red] {exc}")
            row = {
                "answer": "", "retrieved_ids": [], "accessed_ids": [],
                "latency": {}, "tokens": {}, "cost_usd": 0.0,
                "tool_calls": 0, "error": str(exc), "metadata": {},
            }

        row.update({
            "query_id":         qid,
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


def _score_style(v: float) -> str:
    if v >= 0.8:
        return "green"
    if v >= 0.5:
        return "yellow"
    return "red"


def _print_row(row: dict) -> None:
    lat = row.get("latency", {})
    tok = row.get("tokens", {})
    j   = row.get("judge", {})

    line = Text("    ")
    line.append(f"{lat.get('total', 0):.1f}s", style="dim")

    if j:
        faith = j.get("faithfulness", 0)
        corr  = j.get("correctness", 0)
        hall  = j.get("hallucination_count", 0)
        line.append("  faith=", style="dim")
        line.append(f"{faith:.2f}", style=_score_style(faith))
        line.append("  corr=", style="dim")
        line.append(f"{corr:.2f}", style=_score_style(corr))
        line.append(f"  hall={hall}", style="dim")
    else:
        inp = tok.get("input", 0)
        out = tok.get("output", 0)
        line.append(f"  {inp}+{out} tok", style="dim")

    line.append(f"  ${row.get('cost_usd', 0):.4f}", style="dim")
    console.print(line)


# ── Persistence ───────────────────────────────────────────────────────────────

def save_jsonl(rows: list[dict], arch_name: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RUNS_DIR / f"{arch_name}_{ts}.jsonl"
    with open(path, "w") as f:
        for row in rows:
            row = {"arch": arch_name, **row}
            f.write(json.dumps(row, default=str) + "\n")
    console.print(f"  [dim]saved → {path}[/dim]")
    return path
