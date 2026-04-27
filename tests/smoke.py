#!/usr/bin/env python3
"""Smoke test: 3 queries × 3 architectures, side-by-side comparison.

Usage:
  python tests/smoke.py
  python tests/smoke.py --judge     # include LLM-as-judge scoring
  python tests/smoke.py --arch rag  # single architecture
"""
import argparse
import json
import sys
import textwrap
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from architectures.pure_rag import PureRAG
from architectures.pure_agentic import PureAgentic
from architectures.hybrid import Hybrid
from architectures.base import BenchmarkRun
from benchmark.judge import judge as llm_judge

# ── Smoke queries ─────────────────────────────────────────────────────────────

QUERIES = [
    {
        "id": "smoke_factual",
        "category": "factual",
        "question": "What was Abbott's quarterly dividend per share after the December 15, 2017 board increase?",
        "gold_facts": ["quarterly dividend increased to $0.280 per share from $0.265 per share"],
        "forbidden_claims": [],
    },
    {
        "id": "smoke_panoramic",
        "category": "panoramic",
        "question": "What are the key themes and trends that have characterized technology stock performance and investor sentiment across different market cycles?",
        "gold_facts": [
            "FANG stocks have provided leadership in the technology sector",
            "Technology stocks have demonstrated growth stock characteristics",
        ],
        "forbidden_claims": [],
    },
    {
        "id": "smoke_insufficient",
        "category": "insufficient",
        "question": "How did Nvidia's H100 GPU sales contribute to its fiscal 2024 revenue?",
        "gold_facts": [],
        "forbidden_claims": ["H100", "fiscal 2024"],
    },
]

ARCH_MAP = {
    "rag":      PureRAG,
    "agentic":  PureAgentic,
    "hybrid":   Hybrid,
}
ALL_ARCHS = ["rag", "agentic", "hybrid"]

# ── Formatting helpers ────────────────────────────────────────────────────────

W = 78  # total width
COL = 22  # metric label column width


def hr(char="━"):
    print(char * W)


def section(title: str):
    hr()
    print(title)
    hr()


def wrap(text: str, indent: int = 4, width: int = W - 4) -> str:
    lines = []
    for line in text.split("\n"):
        if line.strip():
            lines.extend(textwrap.wrap(line, width=width, initial_indent=" " * indent, subsequent_indent=" " * indent))
        else:
            lines.append("")
    return "\n".join(lines)


def _fmt_judge(j: dict) -> str:
    if not j:
        return "(no judge)"
    faith = j.get("faithfulness", "-")
    corr  = j.get("correctness", "-")
    hall  = j.get("hallucination_count", "-")
    return f"faith={faith:.2f}  corr={corr:.2f}  hall={hall}"


def print_arch_result(arch_name: str, run: BenchmarkRun, verdict: dict) -> None:
    lat = run.latency.get("total", 0)
    tools = run.tool_calls
    cost = run.cost_usd
    print(f"\n  ── {arch_name.upper()} {'─' * (W - 7 - len(arch_name))}")
    print(f"  Latency: {lat:.1f}s  Cost: ${cost:.4f}  Tool calls: {tools}")
    if verdict:
        print(f"  Judge: {_fmt_judge(verdict)}")
        if verdict.get("reasoning"):
            print(f"  Reasoning: {verdict['reasoning'][:80]}")
    print()
    answer_preview = run.answer[:600] if run.answer else "(empty)"
    print(wrap(answer_preview))
    if len(run.answer) > 600:
        print(f"    [... {len(run.answer) - 600} more chars]")


def print_summary_table(arch_names: list[str], runs: list[BenchmarkRun], verdicts: list[dict]) -> None:
    print()
    hr("─")
    # Header
    cols = [f"{n.upper():<18}" for n in arch_names]
    print(f"  {'Metric':<{COL}}" + "".join(cols))
    hr("─")

    def row(label: str, values: list[str]) -> None:
        print(f"  {label:<{COL}}" + "".join(f"{v:<18}" for v in values))

    row("Latency (s)", [f"{r.latency.get('total', 0):.2f}" for r in runs])
    row("Cost (USD)",  [f"${r.cost_usd:.4f}" for r in runs])
    row("Tool calls",  [str(r.tool_calls) for r in runs])
    row("Retrieved",   [str(len(r.retrieved_ids)) for r in runs])
    row("Accessed",    [str(len(r.accessed_ids)) for r in runs])
    if any(verdicts):
        row("Faithfulness",      [f"{v.get('faithfulness', '-'):.2f}" if v else "-" for v in verdicts])
        row("Correctness",       [f"{v.get('correctness', '-'):.2f}"  if v else "-" for v in verdicts])
        row("Hallucinations",    [str(v.get("hallucination_count", "-")) if v else "-" for v in verdicts])
        row("Refusal correct",   [str(v.get("refusal_correct", "-")) if v else "-" for v in verdicts])
    hr("─")


# ── Runner ────────────────────────────────────────────────────────────────────

def run_smoke(arch_names: list[str], use_judge: bool) -> list[dict]:
    # results[query_id][arch_name] = (run, verdict)
    results: dict[str, dict[str, tuple]] = {q["id"]: {} for q in QUERIES}

    # Run one architecture at a time (Qdrant file lock prevents concurrent access)
    for name in arch_names:
        print(f"\nLoading {name}...")
        arch = ARCH_MAP[name]()
        arch.load()
        print(f"  Running {len(QUERIES)} queries...")

        for q in QUERIES:
            print(f"  [{q['id']}] {q['question'][:55]}...", end="", flush=True)
            try:
                run = arch.run_query(q["question"])
            except Exception as exc:
                print(f" ERROR: {exc}")
                run = BenchmarkRun(answer="", error=str(exc))
            else:
                print(f" {run.latency.get('total', 0):.1f}s")

            verdict: dict = {}
            if use_judge and run.answer and not run.error:
                try:
                    v = llm_judge(
                        question=q["question"],
                        gold_facts=q["gold_facts"],
                        forbidden_claims=q["forbidden_claims"],
                        context="",
                        answer=run.answer,
                    )
                    verdict = v.to_dict()
                except Exception as exc:
                    verdict = {"error": str(exc)}

            results[q["id"]][name] = (run, verdict)

        arch.unload()
        print(f"  {name} done.")

    # Print side-by-side per query
    rows = []
    print()
    for q in QUERIES:
        section(f"[{q['category'].upper()}] {q['question']}")
        q_runs: list[BenchmarkRun] = []
        q_verdicts: list[dict] = []

        for name in arch_names:
            run, verdict = results[q["id"]][name]
            print_arch_result(name, run, verdict)
            q_runs.append(run)
            q_verdicts.append(verdict)
            rows.append({
                "query_id": q["id"],
                "question": q["question"],
                "category": q["category"],
                "arch": name,
                "answer": run.answer,
                "latency": run.latency,
                "tokens": run.tokens,
                "cost_usd": run.cost_usd,
                "tool_calls": run.tool_calls,
                "retrieved_ids": run.retrieved_ids,
                "accessed_ids": run.accessed_ids,
                "error": run.error,
                "judge": verdict,
                "gold_facts": q["gold_facts"],
                "forbidden_claims": q["forbidden_claims"],
            })

        print_summary_table(arch_names, q_runs, q_verdicts)
        print()

    return rows


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test: 3 queries × 3 architectures")
    parser.add_argument("--judge", action="store_true", help="Run LLM-as-judge scoring")
    parser.add_argument(
        "--arch", nargs="+", choices=list(ARCH_MAP), default=ALL_ARCHS,
        metavar="ARCH", help="Architectures to run (default: all)",
    )
    args = parser.parse_args()

    print(f"\n{'═' * W}")
    print(f"  SMOKE TEST  |  {len(QUERIES)} queries × {len(args.arch)} architectures")
    print(f"  Judge: {'yes' if args.judge else 'no (pass --judge to enable)'}")
    print(f"{'═' * W}\n")

    rows = run_smoke(args.arch, args.judge)

    # Save results
    out_dir = ROOT / "results" / "smoke"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"smoke_{ts}.json"
    out_path.write_text(json.dumps(rows, indent=2, default=str))

    total_cost = sum(r.get("cost_usd", 0) for r in rows)
    print(f"\n{'═' * W}")
    print(f"  Total cost: ${total_cost:.4f}   Results saved → {out_path.relative_to(ROOT)}")
    print(f"{'═' * W}\n")


if __name__ == "__main__":
    main()
