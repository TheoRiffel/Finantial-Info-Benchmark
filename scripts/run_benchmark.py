#!/usr/bin/env python3
"""Run the RAG benchmark.

Usage:
  python scripts/run_benchmark.py
  python scripts/run_benchmark.py --query "What was Abbott's dividend?"
  python scripts/run_benchmark.py --architectures rag hybrid
  python scripts/run_benchmark.py --eval-set benchmark/eval_set_v2.json
  python scripts/run_benchmark.py --no-judge
  python scripts/run_benchmark.py --no-smoke
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from rich.console import Console
from rich.table import Table
from rich import box

from architectures.pure_rag import PureRAG
from architectures.pure_agentic import PureAgentic
from architectures.hybrid import Hybrid
from benchmark.runner import run_benchmark, save_jsonl, console
from benchmark.metrics import compute_aggregate, compute_by_category

ARCH_MAP = {"rag": PureRAG, "agentic": PureAgentic, "hybrid": Hybrid}
DEFAULT_EVAL = ROOT / "benchmark" / "eval_set_v2.json"
FALLBACK_EVAL = ROOT / "benchmark" / "eval_set.json"
SMOKE_N = 3


def _load_queries(path: Path) -> list[dict]:
    with open(path) as f:
        return json.load(f)["queries"]


def _pick_smoke(queries: list[dict]) -> list[dict]:
    """One query per category, up to SMOKE_N total."""
    seen: set[str] = set()
    picked: list[dict] = []
    for q in queries:
        cat = q.get("category", "")
        if cat not in seen:
            picked.append(q)
            seen.add(cat)
        if len(picked) >= SMOKE_N:
            break
    return picked or queries[:SMOKE_N]


def _print_summary(rows: list[dict], label: str) -> None:
    agg = compute_aggregate(rows)
    errors = sum(1 for r in rows if r.get("error"))

    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 2), show_edge=False)
    t.add_column("k", style="dim", no_wrap=True)
    t.add_column("v", no_wrap=True)

    t.add_row("queries", str(agg.get("n", 0)) + (f"  [red]{errors} errors[/red]" if errors else ""))
    t.add_row("latency", f"{agg.get('latency_mean', 0):.1f} s/q")
    t.add_row("cost", f"${agg.get('cost_total', 0):.4f} total  (${agg.get('cost_mean', 0):.4f}/q)")
    if "faithfulness" in agg:
        t.add_row("faithfulness", f"{agg['faithfulness']:.3f}")
    if "correctness" in agg:
        t.add_row("correctness", f"{agg['correctness']:.3f}")
    if "hallucination_count" in agg:
        t.add_row("hallucinations", f"{agg['hallucination_count']:.1f} avg")

    console.print(f"\n  [bold]{label}[/bold]")
    console.print(t)


def _print_by_category(rows: list[dict]) -> None:
    by_cat = compute_by_category(rows)

    t = Table(box=box.SIMPLE, show_header=True, padding=(0, 2), show_edge=False)
    t.add_column("category", style="dim")
    t.add_column("faith", justify="right")
    t.add_column("corr", justify="right")
    t.add_column("hall", justify="right")
    t.add_column("latency", justify="right", style="dim")

    for cat, stats in sorted(by_cat.items()):
        faith = f"{stats['faithfulness']:.2f}" if "faithfulness" in stats else "—"
        corr  = f"{stats['correctness']:.2f}"  if "correctness"  in stats else "—"
        hall  = f"{stats.get('hallucination_count', 0):.1f}"
        lat   = f"{stats.get('latency_mean', 0):.1f}s"
        t.add_row(cat, faith, corr, hall, lat)

    console.print(t)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run RAG benchmark")
    parser.add_argument(
        "--architectures", nargs="+",
        default=list(ARCH_MAP),
        choices=list(ARCH_MAP),
        metavar="ARCH",
        help="Architectures to run (default: all). Choices: rag agentic hybrid",
    )
    parser.add_argument(
        "--query", "-q", type=str, default=None,
        help="Ask a single question and print the answer (skips benchmark mode)",
    )
    parser.add_argument(
        "--eval-set", type=Path, default=None,
        help="Path to eval set JSON (default: eval_set_v2.json, fallback: eval_set.json)",
    )
    parser.add_argument("--no-judge", action="store_true", help="Skip LLM judge")
    parser.add_argument("--no-smoke", action="store_true", help="Skip smoke test and confirmation")
    args = parser.parse_args()

    # ── Single-query mode ──────────────────────────────────────────────────────
    if args.query:
        archs = args.architectures if len(args.architectures) < len(ARCH_MAP) else ["rag"]
        for arch_name in archs:
            arch = ARCH_MAP[arch_name]()
            with console.status(f"[dim]Loading {arch_name}…[/dim]", spinner="dots"):
                arch.load()
            result = arch.run_query(args.query)
            arch.unload()
            if len(archs) > 1:
                console.rule(f"[bold]{arch_name.upper()}[/bold]")
            console.print(result.answer, markup=False)
            console.print(
                f"\n[dim][{arch_name}  {result.latency['total']:.1f}s  "
                f"${result.cost_usd:.4f}  {result.tool_calls} tool calls][/dim]"
            )
        return

    # ── Resolve eval set ───────────────────────────────────────────────────────
    if args.eval_set:
        eval_path = args.eval_set
    elif DEFAULT_EVAL.exists():
        eval_path = DEFAULT_EVAL
    else:
        eval_path = FALLBACK_EVAL

    if not eval_path.exists():
        console.print(f"[red]Eval set not found:[/red] {eval_path}")
        console.print("Run: python scripts/generate_eval.py")
        sys.exit(1)

    all_queries = _load_queries(eval_path)
    smoke_queries = _pick_smoke(all_queries)
    use_judge = not args.no_judge

    # Header
    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 2), show_edge=False)
    t.add_column("k", style="dim")
    t.add_column("v")
    t.add_row("eval set", f"{eval_path.name}  [dim]({len(all_queries)} queries)[/dim]")
    t.add_row("architectures", "  ".join(f"[bold]{a}[/bold]" for a in args.architectures))
    t.add_row("judge", "[green]yes[/green]" if use_judge else "[dim]no[/dim]")
    console.print(t)

    for arch_name in args.architectures:
        arch = ARCH_MAP[arch_name]()
        console.rule(f"[bold]{arch_name.upper()}[/bold]")

        with console.status("[dim]Loading models / index…[/dim]", spinner="dots"):
            arch.load()

        # ── Smoke test ────────────────────────────────────────────────────────
        if not args.no_smoke:
            console.print(f"\n  [bold]Smoke[/bold] [dim]({len(smoke_queries)} queries)[/dim]")
            smoke_rows = run_benchmark(arch, smoke_queries, use_judge=use_judge)
            _print_summary(smoke_rows, "smoke results")

            smoke_cost = sum(r.get("cost_usd", 0) for r in smoke_rows)
            est_full = smoke_cost / len(smoke_queries) * len(all_queries)
            console.print(
                f"  Estimated full run cost: [bold]~${est_full:.3f}[/bold] "
                f"[dim]({len(all_queries)} queries)[/dim]"
            )

            try:
                answer = input("  Proceed with full run? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "n"
            if answer != "y":
                console.print("  [dim]Skipping full run.[/dim]")
                arch.unload()
                continue

        # ── Full run ──────────────────────────────────────────────────────────
        console.print(f"\n  [bold]Full run[/bold] [dim]({len(all_queries)} queries)[/dim]")
        rows = run_benchmark(arch, all_queries, use_judge=use_judge)
        path = save_jsonl(rows, arch_name)

        _print_summary(rows, f"{arch_name} results")
        _print_by_category(rows)

        arch.unload()

    console.print("\n[bold green]Done.[/bold green]")


if __name__ == "__main__":
    main()
