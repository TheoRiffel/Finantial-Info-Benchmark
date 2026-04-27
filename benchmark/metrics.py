"""Aggregate metrics over benchmark result rows.

Public API:
    compute_aggregate(rows) -> dict
    compute_by_category(rows) -> dict[str, dict]
"""
from collections import defaultdict

_JUDGE_KEYS = ("faithfulness", "correctness", "hallucination_count", "refusal_correct")


def compute_aggregate(rows: list[dict]) -> dict:
    """Mean judge scores + total latency/cost over all rows."""
    if not rows:
        return {}

    sums: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)

    for r in rows:
        j = r.get("judge") or {}
        for k in _JUDGE_KEYS:
            if k in j:
                sums[k] += float(j[k])
                counts[k] += 1

        sums["latency_total"] += r.get("latency", {}).get("total", 0.0)
        sums["cost_usd"]      += r.get("cost_usd", 0.0)
        sums["tool_calls"]    += r.get("tool_calls", 0)
        tok = r.get("tokens") or {}
        sums["tokens_input"]  += tok.get("input", 0)
        sums["tokens_output"] += tok.get("output", 0)
        counts["n"] += 1

    n = counts["n"] or 1
    out: dict = {"n": n}

    for k in _JUDGE_KEYS:
        if counts[k]:
            out[k] = round(sums[k] / counts[k], 4)

    out["latency_mean"] = round(sums["latency_total"] / n, 3)
    out["cost_total"]   = round(sums["cost_usd"], 6)
    out["cost_mean"]    = round(sums["cost_usd"] / n, 6)
    out["tool_calls_mean"] = round(sums["tool_calls"] / n, 2)
    out["tokens_input_total"]  = int(sums["tokens_input"])
    out["tokens_output_total"] = int(sums["tokens_output"])

    return out


def compute_by_category(rows: list[dict]) -> dict[str, dict]:
    """Aggregate per category."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[r.get("category", "unknown")].append(r)
    return {cat: compute_aggregate(group) for cat, group in groups.items()}
