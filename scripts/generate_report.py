#!/usr/bin/env python3
"""Generate REPORT.md from a benchmark results JSON file.

Usage:
  python scripts/generate_report.py                          # latest run
  python scripts/generate_report.py --run run_20240427.json  # specific run
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config


def _avg(values: list) -> float:
    return sum(values) / len(values) if values else 0.0


def _judge_avg(rows: list[dict]) -> str:
    avgs = []
    for r in rows:
        js = r.get("judge_scores", {})
        nums = [v for k, v in js.items() if k != "reasoning" and isinstance(v, (int, float))]
        if nums:
            avgs.append(_avg(nums))
    return f"{_avg(avgs):.2f}" if avgs else "N/A"


def _md_table(rows: list[list]) -> str:
    return "\n".join("| " + " | ".join(str(c) for c in row) + " |" for row in rows)


def generate_report(run_file: Path) -> str:
    with open(run_file) as f:
        data = json.load(f)

    meta = data.get("metadata", {})
    lines: list[str] = [
        "# Benchmark Report",
        "",
        f"**Run**: `{data['run_id']}`  ",
        f"**Timestamp**: {meta.get('timestamp', '')}  ",
        f"**Model**: `{meta.get('model', '')}` (Claude Haiku 4.5)  ",
        f"**Queries**: {meta.get('n_queries', '')}  ",
        "",
        "---",
        "",
        "## Summary",
        "",
    ]

    # ── Comparison table ─────────────────────────────────────────────────────
    headers = [
        "Architecture", "Topic Cov.", "Ticker Rec.", "Citations",
        "Judge (/5)", "Retrieval (s)", "Total (s)",
        "Input Tok.", "Output Tok.", "Cost ($)",
    ]
    table: list[list] = [headers, ["---"] * len(headers)]

    for arch_name, rows in data["architectures"].items():
        valid = [r for r in rows if not r.get("error")]
        if not valid:
            table.append([arch_name] + ["ERROR"] * (len(headers) - 1))
            continue

        table.append([
            f"`{arch_name}`",
            f"{_avg([r.get('topic_coverage', 0) for r in valid]):.1%}",
            f"{_avg([r.get('ticker_recall', 0) for r in valid]):.1%}",
            f"{_avg([r.get('citation_count', 0) for r in valid]):.1f}",
            _judge_avg(valid),
            f"{_avg([r.get('latency', {}).get('retrieval', 0) for r in valid]):.2f}",
            f"{_avg([r.get('latency', {}).get('total', 0) for r in valid]):.2f}",
            f"{_avg([r.get('tokens', {}).get('input', 0) for r in valid]):.0f}",
            f"{_avg([r.get('tokens', {}).get('output', 0) for r in valid]):.0f}",
            f"${_avg([r.get('cost_usd', 0) for r in valid]):.4f}",
        ])

    lines += _md_table(table).split("\n") + [""]

    # ── Cache efficiency ──────────────────────────────────────────────────────
    lines += ["", "### Cache Efficiency", ""]
    cache_headers = ["Architecture", "Cache Read Tok.", "Cache Write Tok.", "Cache Hit Rate"]
    cache_table: list[list] = [cache_headers, ["---"] * len(cache_headers)]
    for arch_name, rows in data["architectures"].items():
        valid = [r for r in rows if not r.get("error")]
        if not valid:
            continue
        cr = _avg([r.get("tokens", {}).get("cache_read",  0) for r in valid])
        cw = _avg([r.get("tokens", {}).get("cache_write", 0) for r in valid])
        ci = _avg([r.get("tokens", {}).get("input",       0) for r in valid])
        total_cacheable = cr + cw + ci
        hit_rate = f"{cr / total_cacheable:.1%}" if total_cacheable > 0 else "N/A"
        cache_table.append([f"`{arch_name}`", f"{cr:.0f}", f"{cw:.0f}", hit_rate])
    lines += _md_table(cache_table).split("\n") + [""]

    # ── Per-query breakdown ───────────────────────────────────────────────────
    lines += ["", "---", "", "## Per-Query Results", ""]

    for arch_name, rows in data["architectures"].items():
        lines += [f"### `{arch_name}`", ""]
        for r in rows:
            lat = r.get("latency", {})
            tok = r.get("tokens", {})
            js  = r.get("judge_scores", {})
            lines += [
                f"**[{r['query_id']}]** {r['question']}",
                "",
                f"- **Timing**: retrieval={lat.get('retrieval', 0):.2f}s  "
                f"generation={lat.get('generation', 0):.2f}s  "
                f"total={lat.get('total', 0):.2f}s",
                f"- **Tokens**: input={tok.get('input', 0)}  output={tok.get('output', 0)}  "
                f"cache_read={tok.get('cache_read', 0)}  cache_write={tok.get('cache_write', 0)}",
                f"- **Cost**: ${r.get('cost_usd', 0):.4f}  "
                f"| tool calls: {r.get('tool_calls', 0)}  "
                f"| citations: {r.get('citation_count', 0)}",
                f"- **Heuristics**: topic_coverage={r.get('topic_coverage', 0):.1%}  "
                f"ticker_recall={r.get('ticker_recall', 0):.1%}",
            ]
            if js and any(isinstance(v, (int, float)) for k, v in js.items() if k != "reasoning"):
                scores = {k: v for k, v in js.items() if k != "reasoning"}
                lines.append(
                    f"- **Judge**: {scores}  →  "
                    f"reasoning: *{js.get('reasoning', '')[:120]}*"
                )
            if r.get("error"):
                lines.append(f"- **ERROR**: {r['error']}")
            else:
                answer_excerpt = r.get("answer", "")[:400]
                if len(r.get("answer", "")) > 400:
                    answer_excerpt += "…"
                lines += ["", f"> {answer_excerpt.replace(chr(10), chr(10) + '> ')}"]
            lines += ["", "---", ""]

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", help="Results filename (e.g. run_20240427_120000.json). Defaults to latest.")
    parser.add_argument("--output", default="REPORT.md")
    args = parser.parse_args()

    results_dir = config.ROOT / "results"
    if args.run:
        run_file = results_dir / args.run
    else:
        files = sorted(results_dir.glob("run_*.json"))
        if not files:
            print("No result files found in results/. Run the benchmark first:")
            print("  python scripts/run_benchmark.py")
            sys.exit(1)
        run_file = files[-1]
        print(f"Using latest run: {run_file.name}")

    report = generate_report(run_file)
    output = config.ROOT / args.output
    output.write_text(report, encoding="utf-8")
    print(f"Report written → {output}")


if __name__ == "__main__":
    main()
