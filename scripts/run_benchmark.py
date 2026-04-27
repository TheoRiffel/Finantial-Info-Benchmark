#!/usr/bin/env python3
"""Run the benchmark.

Usage:
  python scripts/run_benchmark.py                  # all 3 architectures
  python scripts/run_benchmark.py --arch rag       # single architecture
  python scripts/run_benchmark.py --no-judge       # skip LLM judge
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from architectures.pure_rag import PureRAG
from architectures.pure_agentic import PureAgentic
from architectures.hybrid import Hybrid
from benchmark.runner import run_benchmark, save_results

ARCH_MAP = {"rag": PureRAG, "agentic": PureAgentic, "hybrid": Hybrid}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--arch",
        default="all",
        choices=["all", "rag", "agentic", "hybrid"],
        help="Architecture(s) to benchmark (default: all)",
    )
    parser.add_argument(
        "--no-judge",
        action="store_true",
        help="Skip LLM-as-judge scoring (faster, no extra API calls)",
    )
    args = parser.parse_args()

    archs = (
        [cls() for cls in ARCH_MAP.values()]
        if args.arch == "all"
        else [ARCH_MAP[args.arch]()]
    )

    results = run_benchmark(archs, use_judge=not args.no_judge)
    path = save_results(results)
    print(f"\nDone. Generate report with:")
    print(f"  python scripts/generate_report.py --run {path.name}")


if __name__ == "__main__":
    main()
