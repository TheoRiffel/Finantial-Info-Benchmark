#!/usr/bin/env python3
"""Generate REPORT.md + plots from benchmark results.

Loads JSONL run files from results/baseline/ (and optionally results/contextual/)
and produces a Markdown report with three matplotlib plots.

Usage:
  python scripts/generate_report.py
  python scripts/generate_report.py --baseline results/baseline --compare results/contextual
  python scripts/generate_report.py --no-plots
  python scripts/generate_report.py --no-failures   # skip Haiku failure categorization
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import seaborn as sns

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import config
from shared.llm import get_client

PLOTS_DIR = ROOT / "results" / "plots"

# ── Consistent colours / labels per architecture ───────────────────────────────

_COLORS = {
    "rag":          "#2196F3",
    "pure_rag":     "#2196F3",
    "agentic":      "#FF9800",
    "pure_agentic": "#FF9800",
    "hybrid":       "#4CAF50",
}
_LABELS = {
    "rag":          "Pure RAG",
    "pure_rag":     "Pure RAG",
    "agentic":      "Pure Agentic",
    "pure_agentic": "Pure Agentic",
    "hybrid":       "Hybrid",
}

def _color(arch: str) -> str:
    return _COLORS.get(arch, "#9C27B0")

def _label(arch: str) -> str:
    return _LABELS.get(arch, arch)


# ── Data loading ───────────────────────────────────────────────────────────────

def _load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for f in sorted(path.glob("*.jsonl")):
        # filename is <arch>_<timestamp>.jsonl — use as fallback when arch missing
        arch_from_name = f.stem.rsplit("_", 2)[0]
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    row = json.loads(line)
                    row.setdefault("arch", arch_from_name)
                    rows.append(row)
    return rows


def _load_json(path: Path) -> list[dict]:
    """Handle old run_*.json dicts with an 'architectures' key."""
    rows: list[dict] = []
    for f in sorted(path.glob("*.json")):
        if f.name.startswith("."):
            continue
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        if isinstance(d, list):
            rows.extend(d)
        elif isinstance(d, dict) and "architectures" in d:
            for arch, arch_rows in d["architectures"].items():
                for r in arch_rows:
                    r = dict(r)
                    r.setdefault("arch", arch)
                    # normalise judge_scores -> judge
                    if "judge_scores" in r and "judge" not in r:
                        r["judge"] = r.pop("judge_scores")
                    rows.append(r)
    return rows


def load_dir(path: Path) -> list[dict]:
    rows = _load_jsonl(path)
    if not rows:
        rows = _load_json(path)
    return rows


def group_by_arch(rows: list[dict]) -> dict[str, list[dict]]:
    g: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        g[r.get("arch", "unknown")].append(r)
    return dict(g)


# ── Metrics helpers ────────────────────────────────────────────────────────────

def _judge_vals(rows: list[dict], key: str) -> list[float]:
    return [
        float(r["judge"][key])
        for r in rows
        if (r.get("judge") or {}).get(key) is not None
    ]


def quality_score(row: dict) -> float | None:
    j = row.get("judge") or {}
    vals = [float(j[k]) for k in ("faithfulness", "correctness") if j.get(k) is not None]
    return sum(vals) / len(vals) if vals else None


def _mean(vals: list[float]) -> float | None:
    return float(np.mean(vals)) if vals else None


def faithfulness_mean(rows: list[dict]) -> float | None:
    return _mean(_judge_vals(rows, "faithfulness"))


def correctness_mean(rows: list[dict]) -> float | None:
    return _mean(_judge_vals(rows, "correctness"))


def quality_mean(rows: list[dict]) -> float | None:
    scores = [s for r in rows if (s := quality_score(r)) is not None]
    return _mean(scores)


def latency_p50(rows: list[dict]) -> float:
    lats = [r.get("latency", {}).get("total", 0.0) for r in rows]
    return float(np.percentile(lats, 50)) if lats else 0.0


def cost_per_query(rows: list[dict]) -> float:
    costs = [r.get("cost_usd", 0.0) for r in rows]
    return float(np.mean(costs)) if costs else 0.0


def _fmt(v: float | None, spec: str = ".3f") -> str:
    return format(v, spec) if v is not None else "N/A"


# ── Pareto frontier (minimise cost, maximise quality) ─────────────────────────

def pareto_indices(points: list[tuple[float, float]]) -> list[int]:
    dominated: set[int] = set()
    for i, (ci, qi) in enumerate(points):
        for j, (cj, qj) in enumerate(points):
            if i == j:
                continue
            if cj <= ci and qj >= qi and (cj < ci or qj > qi):
                dominated.add(i)
                break
    return [i for i in range(len(points)) if i not in dominated]


# ── Plot helpers ───────────────────────────────────────────────────────────────

_RC = {
    "font.family": "sans-serif",
    "font.size": 9,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.alpha": 0.25,
    "figure.dpi": 130,
}


def _save(fig: plt.Figure, name: str) -> Path:
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    out = PLOTS_DIR / name
    fig.tight_layout()
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"  → {out}")
    return out


# ── Plot a: bar quality ────────────────────────────────────────────────────────

def plot_bar_quality(
    configs: dict[str, dict[str, list[dict]]],
) -> Path:
    plt.rcParams.update(_RC)

    archs = sorted({a for d in configs.values() for a in d})
    cfg_names = list(configs.keys())
    n = len(cfg_names)
    x = np.arange(len(archs))
    width = 0.32
    offsets = np.linspace(-(n - 1) * width / 2, (n - 1) * width / 2, n)

    fig, ax = plt.subplots(figsize=(7, 4))

    for ci, cfg_name in enumerate(cfg_names):
        arch_rows = configs[cfg_name]
        vals = [quality_mean(arch_rows.get(a, [])) or 0.0 for a in archs]
        is_base = ci == 0
        bars = ax.bar(
            x + offsets[ci], vals, width * 0.92,
            color=[_color(a) for a in archs],
            alpha=0.90 if is_base else 0.50,
            hatch="" if is_base else "///",
            edgecolor="#fff" if is_base else "#aaa",
            linewidth=0.8,
        )
        for bar, v in zip(bars, vals):
            if v > 0.005:
                ax.text(
                    bar.get_x() + bar.get_width() / 2, v + 0.015,
                    f"{v:.2f}", ha="center", va="bottom", fontsize=7.5,
                )

    ax.set_xticks(x)
    ax.set_xticklabels([_label(a) for a in archs])
    ax.set_ylabel("Quality  (faithfulness + correctness) / 2")
    ax.set_ylim(0, 1.18)
    ax.set_title("Quality by Architecture", pad=10)

    # Arch-colour legend
    arch_patches = [mpatches.Patch(facecolor=_color(a), label=_label(a)) for a in archs]
    # Config legend (solid vs hatched)
    cfg_patches = [
        mpatches.Patch(facecolor="#888", alpha=0.9, label=cfg_names[0]),
        mpatches.Patch(facecolor="#888", alpha=0.5, hatch="///", label=cfg_names[1] if n > 1 else ""),
    ] if n > 1 else []

    handles = arch_patches + cfg_patches
    ax.legend(handles=handles, fontsize=7.5, frameon=False, ncol=2, loc="upper right")

    return _save(fig, "bar_quality.png")


# ── Plot b: scatter cost vs quality with Pareto ────────────────────────────────

def plot_scatter_cost_quality(
    configs: dict[str, dict[str, list[dict]]],
) -> Path:
    plt.rcParams.update(_RC)

    markers = ["o", "^", "s", "D"]
    fig, ax = plt.subplots(figsize=(6, 5))

    all_pts: list[tuple[float, float]] = []
    pt_meta: list[dict] = []

    for ci, (cfg_name, arch_rows) in enumerate(configs.items()):
        mk = markers[ci % len(markers)]
        for arch, rows in arch_rows.items():
            cost = cost_per_query(rows)
            q = quality_mean(rows)
            if q is None:
                continue
            all_pts.append((cost, q))
            pt_meta.append({"arch": arch, "cfg": cfg_name, "marker": mk})
            ax.scatter(cost, q, color=_color(arch), marker=mk, s=100, zorder=4,
                       label=f"{_label(arch)} ({cfg_name})")
            ax.annotate(
                f"{_label(arch)}\n({cfg_name})",
                (cost, q), textcoords="offset points", xytext=(7, 4),
                fontsize=7, color=_color(arch),
            )

    # Pareto frontier
    if len(all_pts) > 1:
        pf = pareto_indices(all_pts)
        frontier = sorted([all_pts[i] for i in pf], key=lambda p: p[0])
        if frontier:
            px, py = zip(*frontier)
            # staircase line
            step_x = list(px) + [max(px)]
            step_y = list(py) + [min(py)]
            ax.step(step_x, step_y, where="post", color="#555",
                    linewidth=1.3, linestyle="--", label="Pareto frontier", zorder=2)

    ax.set_xlabel("Cost per query (USD)")
    ax.set_ylabel("Quality score")
    ax.set_title("Cost vs Quality")
    ax.legend(fontsize=7, frameon=False, loc="lower right")

    return _save(fig, "scatter_cost_quality.png")


# ── Plot c: heatmap quality × category ────────────────────────────────────────

def plot_heatmap_category(
    configs: dict[str, dict[str, list[dict]]],
    cfg_name: str | None = None,
) -> Path:
    plt.rcParams.update(_RC)

    cfg_name = cfg_name or list(configs.keys())[0]
    arch_rows = configs[cfg_name]
    archs = sorted(arch_rows.keys())

    cats: set[str] = set()
    for rows in arch_rows.values():
        for r in rows:
            c = r.get("category", "")
            if c:
                cats.add(c)
    cats_sorted = sorted(cats)

    matrix = np.full((len(cats_sorted), len(archs)), np.nan)
    for j, arch in enumerate(archs):
        by_cat: dict[str, list[float]] = defaultdict(list)
        for r in arch_rows[arch]:
            q = quality_score(r)
            cat = r.get("category", "")
            if q is not None and cat:
                by_cat[cat].append(q)
        for i, cat in enumerate(cats_sorted):
            if by_cat[cat]:
                matrix[i, j] = float(np.mean(by_cat[cat]))

    # Highlight winner per row
    winner_mask = np.zeros_like(matrix, dtype=bool)
    for i in range(len(cats_sorted)):
        row_vals = matrix[i]
        valid = row_vals[~np.isnan(row_vals)]
        if valid.size:
            best = np.nanmax(row_vals)
            winner_mask[i] = (row_vals == best) & ~np.isnan(row_vals)

    fig, ax = plt.subplots(figsize=(max(5, len(archs) * 2.0), max(3, len(cats_sorted) + 1.5)))
    sns.heatmap(
        matrix, ax=ax,
        xticklabels=[_label(a) for a in archs],
        yticklabels=cats_sorted,
        annot=True, fmt=".2f",
        vmin=0, vmax=1,
        cmap="RdYlGn",
        linewidths=0.6,
        mask=np.isnan(matrix),
        cbar_kws={"label": "Quality", "shrink": 0.8},
    )

    # Bold border around winners
    for i in range(len(cats_sorted)):
        for j in range(len(archs)):
            if winner_mask[i, j]:
                ax.add_patch(plt.Rectangle(
                    (j, i), 1, 1,
                    fill=False, edgecolor="#222", linewidth=2.2, zorder=5,
                ))

    ax.set_title(f"Quality by Category  —  {cfg_name}", pad=10)
    ax.set_xlabel("")
    ax.set_ylabel("")

    return _save(fig, "heatmap_category.png")


# ── Failure categorization ─────────────────────────────────────────────────────

_FAIL_SYSTEM = (
    "You are an expert at diagnosing RAG system failures. "
    "Classify the failure mode in 2-5 words. "
    "Choose the most accurate label. Examples: retrieval miss, hallucination, "
    "incomplete answer, reasoning error, refusal error, context gap, no judge data."
)


def _categorize(row: dict) -> str:
    j = row.get("judge") or {}
    if not j:
        return "no judge data"
    prompt = (
        f"Question: {row.get('question', '')}\n"
        f"Answer (first 300 chars): {str(row.get('answer', ''))[:300]}\n"
        f"Faithfulness: {j.get('faithfulness', '?')}\n"
        f"Correctness: {j.get('correctness', '?')}\n"
        f"Hallucinations: {j.get('hallucination_count', '?')}\n"
        f"Judge note: {j.get('reasoning', '')}\n\n"
        "Classify this failure in 2-5 words."
    )
    try:
        resp = get_client().messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=20,
            system=[{"type": "text", "text": _FAIL_SYSTEM,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as exc:
        return f"error: {exc}"


def top_failures(rows: list[dict], n: int = 3) -> list[dict]:
    scored = [(quality_score(r), r) for r in rows]
    # errors first, then lowest quality
    errors = [r for r in rows if r.get("error")]
    with_score = sorted(
        [(s, r) for s, r in scored if s is not None],
        key=lambda x: x[0],
    )
    candidates = errors + [r for _, r in with_score]
    seen: set[int] = set()
    out: list[dict] = []
    for r in candidates:
        if id(r) not in seen:
            out.append(r)
            seen.add(id(r))
        if len(out) >= n:
            break
    return out


# ── Markdown table helper ──────────────────────────────────────────────────────

def _md_row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


# ── Report assembly ────────────────────────────────────────────────────────────

def build_report(
    configs: dict[str, dict[str, list[dict]]],
    failures: dict[str, list[dict]],   # arch -> rows (with "failure_category" key)
    plot_paths: list[Path],
) -> str:
    lines: list[str] = [
        "# Benchmark Report",
        "",
        f"**Configs**: {', '.join(f'`{c}`' for c in configs)}  ",
        "",
        "---",
        "",
        "## Summary Table",
        "",
        _md_row(["Architecture", "Config", "Faithfulness", "Correctness",
                 "Latency p50 (s)", "Cost / query ($)"]),
        _md_row(["---"] * 6),
    ]

    for cfg_name, arch_rows in configs.items():
        for arch in sorted(arch_rows):
            rows = [r for r in arch_rows[arch] if not r.get("error")]
            faith = faithfulness_mean(rows)
            corr = correctness_mean(rows)
            lat = latency_p50(rows)
            cost = cost_per_query(rows)
            lines.append(_md_row([
                f"`{_label(arch)}`",
                cfg_name,
                _fmt(faith),
                _fmt(corr),
                f"{lat:.2f}",
                f"${cost:.5f}",
            ]))

    lines += ["", "---", "", "## Plots", ""]
    for p in plot_paths:
        try:
            rel = p.relative_to(ROOT)
        except ValueError:
            rel = p
        lines += [f"![{p.stem}]({rel})", ""]

    lines += ["", "---", "", "## Top 3 Failures per Architecture", ""]

    if failures:
        for arch in sorted(failures):
            lines += [f"### {_label(arch)}", ""]
            for i, row in enumerate(failures[arch], 1):
                j = row.get("judge") or {}
                cat = row.get("failure_category", "—")
                q_id = row.get("query_id", "?")
                question = row.get("question", "")[:90]
                faith_s = _fmt(j.get("faithfulness"), ".2f")
                corr_s = _fmt(j.get("correctness"), ".2f")
                hall = j.get("hallucination_count", "?")
                note = j.get("reasoning") or row.get("error") or ""
                lines += [
                    f"**{i}. [{q_id}]** {question}",
                    f"- Failure type: `{cat}`  ·  "
                    f"faith={faith_s}  corr={corr_s}  hall={hall}",
                    f"- *{note[:160]}*",
                    "",
                ]
    else:
        lines.append("*(run without `--no-failures` to see LLM-categorized failures)*")
        lines.append("")

    return "\n".join(lines)


# ── Main ───────────────────────────────────────────────────────────────────────

def _default_baseline() -> Path:
    """Return the most populated results directory, preferring runs/ over smoke/."""
    candidates = [
        ROOT / "results" / "runs",
        ROOT / "results" / "smoke",
    ]
    for c in candidates:
        if c.exists() and any(c.iterdir()):
            return c
    return ROOT / "results" / "runs"


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate benchmark report + plots")
    ap.add_argument("--baseline", type=Path,
                    default=None,
                    help="Directory with result files (default: results/runs/, falls back to results/smoke/)")
    ap.add_argument("--compare", type=Path, default=None,
                    help="Optional second config directory (e.g. results/contextual/)")
    ap.add_argument("--output", default="REPORT.md",
                    help="Output Markdown file (default: REPORT.md)")
    ap.add_argument("--no-plots", action="store_true",
                    help="Skip matplotlib plot generation")
    ap.add_argument("--no-failures", action="store_true",
                    help="Skip Haiku failure categorization")
    args = ap.parse_args()

    if args.baseline is None:
        args.baseline = _default_baseline()

    # ── Load data ──────────────────────────────────────────────────────────────
    configs: dict[str, dict[str, list[dict]]] = {}

    if not args.baseline.exists():
        sys.exit(
            f"No results directory found (tried results/runs/ and results/smoke/).\n"
            "Run the benchmark first:\n"
            "  python scripts/run_benchmark.py"
        )

    baseline_rows = load_dir(args.baseline)
    if not baseline_rows:
        sys.exit(f"No JSONL (or JSON) result files found in {args.baseline}")

    baseline_name = args.baseline.name
    configs[baseline_name] = group_by_arch(baseline_rows)
    print(f"Loaded {len(baseline_rows)} rows from '{baseline_name}' "
          f"({', '.join(sorted(configs[baseline_name]))})")

    if args.compare and args.compare.exists():
        cmp_rows = load_dir(args.compare)
        if cmp_rows:
            cmp_name = args.compare.name
            configs[cmp_name] = group_by_arch(cmp_rows)
            print(f"Loaded {len(cmp_rows)} rows from '{cmp_name}' "
                  f"({', '.join(sorted(configs[cmp_name]))})")

    # ── Plots ──────────────────────────────────────────────────────────────────
    plot_paths: list[Path] = []
    if not args.no_plots:
        print("\nGenerating plots...")
        plot_paths.append(plot_bar_quality(configs))
        plot_paths.append(plot_scatter_cost_quality(configs))
        plot_paths.append(plot_heatmap_category(configs))

    # ── Failures ───────────────────────────────────────────────────────────────
    failures: dict[str, list[dict]] = {}
    if not args.no_failures:
        print("\nCategorizing failures (Haiku)...")
        for arch, rows in configs[baseline_name].items():
            worst = top_failures(rows, n=3)
            for row in worst:
                snippet = row.get("question", "")[:55]
                tag = _categorize(row)
                row["failure_category"] = tag
                print(f"  [{_label(arch)}] {snippet!r}  →  {tag}")
            failures[arch] = worst

    # ── Write report ───────────────────────────────────────────────────────────
    report = build_report(configs, failures, plot_paths)
    out = ROOT / args.output
    out.write_text(report, encoding="utf-8")
    print(f"\nReport written → {out}")


if __name__ == "__main__":
    main()
