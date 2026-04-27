#!/usr/bin/env python3
"""Generate benchmark/eval_set_v2.json — 20 questions across 4 categories.

Categories (5 each):
  factual_simple  — single chunk answers the question
  comparative     — "X vs Y", requires two chunks
  panoramic       — broad sentiment/outlook, many chunks
  insufficient    — topic NOT in corpus (tests refusal)

Usage:
  python scripts/generate_eval.py
  python scripts/generate_eval.py --seed 7 --out benchmark/my_eval.json
"""
import argparse
import json
import pickle
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import config

QUESTIONS_PER_CATEGORY = 5
OUTPUT_DEFAULT = ROOT / "benchmark" / "eval_set_v2.json"

# ── Haiku helper ──────────────────────────────────────────────────────────────

_SYSTEM = (
    "You are an eval-set author for a financial RAG benchmark. "
    "Respond with valid JSON only — no markdown fences, no extra prose."
)


def _call(prompt: str, client) -> str:
    resp = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=512,
        system=[{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


def _parse(raw: str) -> dict | None:
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group())
    except json.JSONDecodeError:
        return None


# ── Category generators ───────────────────────────────────────────────────────

def gen_factual_simple(chunks: list[dict], client) -> list[dict]:
    """One chunk → one specific verifiable question."""
    pool = [c for c in chunks if len(c["raw_text"]) > 400 and c.get("tickers")]
    random.shuffle(pool)
    results: list[dict] = []

    for chunk in pool:
        if len(results) >= QUESTIONS_PER_CATEGORY:
            break
        prompt = (
            f"Financial news excerpt — chunk_id: {chunk['chunk_id']}\n"
            f"Title: {chunk['doc_title']}\n"
            f"Date: {chunk['date']}\n\n"
            f"{chunk['raw_text'][:1400]}\n\n"
            "Generate ONE specific factual question answerable ONLY from this text "
            "(a concrete number, event, decision, or named metric). "
            "Set suitable=false if the text has no specific verifiable fact.\n\n"
            'Return: {"question":"...","gold_facts":["verbatim fact 1","verbatim fact 2"],'
            '"suitable":true}'
        )
        data = _parse(_call(prompt, client))
        if data and data.get("suitable"):
            results.append({
                "question": data["question"],
                "gold_chunk_ids": [chunk["chunk_id"]],
                "gold_facts": data.get("gold_facts", []),
                "forbidden_claims": [],
            })
            print(f"    [factual_simple {len(results)}] {data['question'][:72]}")

    return results[:QUESTIONS_PER_CATEGORY]


def gen_comparative(chunks: list[dict], client) -> list[dict]:
    """Pair two chunks with different tickers → X vs Y question."""
    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for c in chunks:
        if len(c["raw_text"]) > 400:
            for t in c.get("tickers", []):
                by_ticker[t].append(c)

    # Only tickers with ≥1 good chunk; exclude broad indices
    skip = {"S&P 500", "Dow Jones", "Nasdaq", "Russell 2000"}
    good_tickers = [t for t, cs in by_ticker.items() if cs and t not in skip]

    results: list[dict] = []
    seen_pairs: set[frozenset] = set()
    attempts = 0

    while len(results) < QUESTIONS_PER_CATEGORY and attempts < 40:
        attempts += 1
        if len(good_tickers) < 2:
            break
        t1, t2 = random.sample(good_tickers, 2)
        pair_key = frozenset([t1, t2])
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)

        c1 = random.choice(by_ticker[t1])
        c2 = random.choice(by_ticker[t2])
        if c1["doc_id"] == c2["doc_id"]:
            continue

        prompt = (
            f"Two financial news excerpts:\n\n"
            f"[A] chunk_id={c1['chunk_id']} | {c1['doc_title']} | {c1['date']}\n"
            f"{c1['raw_text'][:700]}\n\n"
            f"[B] chunk_id={c2['chunk_id']} | {c2['doc_title']} | {c2['date']}\n"
            f"{c2['raw_text'][:700]}\n\n"
            f"Generate ONE comparison question about {t1} vs {t2} that requires BOTH excerpts. "
            "Set suitable=false if there's no meaningful contrast between them.\n\n"
            'Return: {"question":"...","gold_facts":["fact from A","fact from B"],"suitable":true}'
        )
        data = _parse(_call(prompt, client))
        if data and data.get("suitable"):
            results.append({
                "question": data["question"],
                "gold_chunk_ids": [c1["chunk_id"], c2["chunk_id"]],
                "gold_facts": data.get("gold_facts", []),
                "forbidden_claims": [],
            })
            print(f"    [comparative {len(results)}] {data['question'][:72]}")

    return results[:QUESTIONS_PER_CATEGORY]


def gen_panoramic(chunks: list[dict], client) -> list[dict]:
    """5 chunks on a theme → broad sentiment/outlook question."""
    topic_seeds = [
        ("technology stocks", ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "FB"]),
        ("oil and energy markets", ["XOM", "CVX", "BP", "OPEC", "oil", "crude"]),
        ("Federal Reserve monetary policy", ["Federal Reserve", "Fed", "interest rate", "FOMC", "inflation"]),
        ("global trade tensions", ["trade war", "tariff", "China", "Trump", "NAFTA", "WTO"]),
        ("banking sector", ["JPMorgan", "Goldman Sachs", "Citigroup", "Bank of America", "Wells Fargo"]),
        ("emerging markets", ["emerging market", "China", "India", "Brazil", "EM"]),
    ]

    results: list[dict] = []

    for topic_name, keywords in topic_seeds:
        if len(results) >= QUESTIONS_PER_CATEGORY:
            break

        # Gather chunks matching any keyword (title or text)
        related: list[dict] = []
        for c in chunks:
            blob = (c["raw_text"] + " " + c["doc_title"]).lower()
            tickers = c.get("tickers", [])
            if any(kw.lower() in blob or kw in tickers for kw in keywords):
                related.append(c)

        if len(related) < 5:
            continue

        sample = random.sample(related, min(6, len(related)))
        excerpts = "\n\n".join(
            f"[{i+1}] ({c['doc_title']}, {c['date']}):\n{c['raw_text'][:500]}"
            for i, c in enumerate(sample)
        )

        prompt = (
            f"Topic: {topic_name}\n\n"
            f"{excerpts}\n\n"
            "Generate ONE broad panoramic question about the overall sentiment, trend, or outlook "
            "for this topic that CANNOT be answered from a single document alone. "
            "Examples: 'What is the general analyst outlook for...?' or 'What key themes emerge "
            "from coverage of...?'\n\n"
            'Return: {"question":"...","gold_facts":["theme 1","theme 2","theme 3"],"suitable":true}'
        )
        data = _parse(_call(prompt, client))
        if data and data.get("suitable"):
            results.append({
                "question": data["question"],
                "gold_chunk_ids": [c["chunk_id"] for c in sample],
                "gold_facts": data.get("gold_facts", []),
                "forbidden_claims": [],
            })
            print(f"    [panoramic {len(results)}] {data['question'][:72]}")

    return results[:QUESTIONS_PER_CATEGORY]


def gen_insufficient() -> list[dict]:
    """Hardcoded questions about events clearly outside the corpus (post-2019)."""
    # Corpus is ~2017-2018 Reuters financial news.
    # These reference events that definitely cannot be in it.
    items = [
        {
            "question": "How did Nvidia's H100 GPU sales contribute to its fiscal 2024 revenue?",
            "gold_chunk_ids": [],
            "gold_facts": [],
            "forbidden_claims": ["H100", "fiscal 2024", "Nvidia 2024 revenue"],
        },
        {
            "question": "What impact did the collapse of Silicon Valley Bank in March 2023 have on tech startups?",
            "gold_chunk_ids": [],
            "gold_facts": [],
            "forbidden_claims": ["SVB collapse", "Silicon Valley Bank failure 2023"],
        },
        {
            "question": "What were Apple's iPhone 15 unit sales in its launch quarter?",
            "gold_chunk_ids": [],
            "gold_facts": [],
            "forbidden_claims": ["iPhone 15", "2023 iPhone launch"],
        },
        {
            "question": "How did the Federal Reserve's rate hikes in 2022 affect the housing market?",
            "gold_chunk_ids": [],
            "gold_facts": [],
            "forbidden_claims": ["2022 rate hikes", "Fed 2022", "housing market 2022"],
        },
        {
            "question": "What were the main causes of the crypto market crash in 2022, including the FTX collapse?",
            "gold_chunk_ids": [],
            "gold_facts": [],
            "forbidden_claims": ["FTX", "crypto crash 2022", "Sam Bankman-Fried"],
        },
    ]
    for item in items:
        print(f"    [insufficient] {item['question'][:72]}")
    return items


# ── Assembly ──────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate eval_set_v2.json")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=OUTPUT_DEFAULT)
    args = parser.parse_args()

    random.seed(args.seed)

    chunks_path = config.INDEX_DIR / "chunks.pkl"
    if not chunks_path.exists():
        sys.exit(f"Chunks not found: {chunks_path}\nBuild the index first.")

    with open(chunks_path, "rb") as f:
        chunks: list[dict] = pickle.load(f)
    print(f"Loaded {len(chunks)} chunks from index")

    from shared.llm import get_client
    client = get_client()

    categories: list[tuple[str, list[dict]]] = []

    print("\n[1/4] factual_simple")
    categories.append(("factual_simple", gen_factual_simple(chunks, client)))

    print("\n[2/4] comparative")
    categories.append(("comparative", gen_comparative(chunks, client)))

    print("\n[3/4] panoramic")
    categories.append(("panoramic", gen_panoramic(chunks, client)))

    print("\n[4/4] insufficient (hardcoded)")
    categories.append(("insufficient", gen_insufficient()))

    # Warn if any category is short
    for cat, items in categories:
        if len(items) < QUESTIONS_PER_CATEGORY:
            print(f"  WARNING: {cat} has only {len(items)}/{QUESTIONS_PER_CATEGORY} questions")

    queries: list[dict] = []
    counter = 1
    for cat, items in categories:
        for item in items:
            queries.append({
                "id": f"q{counter:02d}",
                "question": item["question"],
                "category": cat,
                "gold_chunk_ids": item["gold_chunk_ids"],
                "gold_facts": item["gold_facts"],
                "forbidden_claims": item["forbidden_claims"],
            })
            counter += 1

    out = {"queries": queries}
    args.out.write_text(json.dumps(out, indent=2))
    print(f"\nWrote {len(queries)} questions → {args.out}")
    for cat, items in categories:
        print(f"  {cat}: {len(items)}")


if __name__ == "__main__":
    main()
