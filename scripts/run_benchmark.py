#!/usr/bin/env python3
"""Run the RAG benchmark.

Usage:
  python scripts/run_benchmark.py
  python scripts/run_benchmark.py --architectures rag hybrid
  python scripts/run_benchmark.py --eval-set benchmark/eval_set_v2.json
  python scripts/run_benchmark.py --no-judge
  python scripts/run_benchmark.py --no-smoke   # skip confirmation step
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from architectures.pure_rag import PureRAG
from architectures.pure_agentic import PureAgentic
from architectures.hybrid import Hybrid
from benchmark.runner import run_benchmark, save_jsonl
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
    print(f"\n  ── {label} ──")
    print(f"    n={agg.get('n', 0)}  errors={errors}  "
          f"latency={agg.get('latency_mean', 0):.1f}s/q  "
          f"cost=${agg.get('cost_total', 0):.4f}")
    for k in ("faithfulness", "correctness", "hallucination_count"):
        if k in agg:
            print(f"    {k}={agg[k]:.3f}")


def _print_by_category(rows: list[dict]) -> None:
    by_cat = compute_by_category(rows)
    print(f"\n  ── by category ──")
    for cat, stats in sorted(by_cat.items()):
        faith = f"{stats['faithfulness']:.2f}" if "faithfulness" in stats else "  — "
        corr  = f"{stats['correctness']:.2f}"  if "correctness"  in stats else "  — "
        hall  = f"{stats.get('hallucination_count', 0):.1f}"
        print(f"    {cat:<22} faith={faith}  corr={corr}  hall={hall}")


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
        "--eval-set", type=Path, default=None,
        help="Path to eval set JSON (default: eval_set_v2.json, fallback: eval_set.json)",
    )
    parser.add_argument("--no-judge", action="store_true", help="Skip LLM judge")
    parser.add_argument("--no-smoke", action="store_true", help="Skip smoke test and confirmation")
    args = parser.parse_args()

    # Resolve eval set
    if args.eval_set:
        eval_path = args.eval_set
    elif DEFAULT_EVAL.exists():
        eval_path = DEFAULT_EVAL
    else:
        eval_path = FALLBACK_EVAL

    if not eval_path.exists():
        sys.exit(f"Eval set not found: {eval_path}\nRun: python scripts/generate_eval.py")

    all_queries = _load_queries(eval_path)
    smoke_queries = _pick_smoke(all_queries)
    use_judge = not args.no_judge

    print(f"Eval set : {eval_path.name}  ({len(all_queries)} queries)")
    print(f"Architectures : {', '.join(args.architectures)}")
    print(f"Judge : {'yes' if use_judge else 'no'}")

    for arch_name in args.architectures:
        arch = ARCH_MAP[arch_name]()
        print(f"\n{'='*60}")
        print(f"  Architecture: {arch_name.upper()}")
        print(f"{'='*60}")
        print("  Loading models/index...")
        arch.load()

        # ── Smoke test ────────────────────────────────────────────────────────
        if not args.no_smoke:
            print(f"\n  ── Smoke test ({len(smoke_queries)} queries) ──")
            smoke_rows = run_benchmark(arch, smoke_queries, use_judge=use_judge)
            _print_summary(smoke_rows, "smoke results")

            smoke_cost = sum(r.get("cost_usd", 0) for r in smoke_rows)
            est_full = smoke_cost / len(smoke_queries) * len(all_queries)
            print(f"\n  Estimated full run cost: ~${est_full:.3f}  ({len(all_queries)} queries)")

            try:
                answer = input("  Proceed with full run? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "n"
            if answer != "y":
                print("  Skipping full run.")
                arch.unload()
                continue

        # ── Full run ──────────────────────────────────────────────────────────
        print(f"\n  ── Full run ({len(all_queries)} queries) ──")
        rows = run_benchmark(arch, all_queries, use_judge=use_judge)
        path = save_jsonl(rows, arch_name)

        _print_summary(rows, f"final results: {arch_name}")
        _print_by_category(rows)
        print(f"\n  JSONL → {path}")

        arch.unload()

    print("\nDone.")


if __name__ == "__main__":
    main()
