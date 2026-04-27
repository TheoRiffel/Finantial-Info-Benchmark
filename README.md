# RAG Benchmark

Compares three retrieval-augmented generation architectures on 10 financial Q&A queries.
All LLM calls use **Claude Haiku 4.5** (`claude-haiku-4-5`) via the Anthropic API.

## Architectures

| Name | Description |
|---|---|
| `pure_rag` | Hybrid search (dense + BM25 + RRF) → cross-encoder rerank → single Haiku call with context |
| `pure_agentic` | Haiku with `grep_corpus` + `read_article` tools; agent iterates until it has enough context |
| `hybrid` | RAG pre-retrieval supplies initial context; agent can use tools to supplement |

## Prerequisites

1. **Existing index** — the benchmark reads the index built by RAG_finances:
   ```
   cd ../RAG_finances
   python -m ingestion.download_data   # downloads ~473 articles
   python -m ingestion.indexer         # builds Qdrant DB + BM25 index
   ```
   Override locations with env vars `CORPUS_DIR` and `INDEX_DIR`.

2. **Anthropic API key**:
   ```bash
   export ANTHROPIC_API_KEY=sk-ant-...
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

## Running

```bash
# Full benchmark — all 3 architectures, with LLM judge
python scripts/run_benchmark.py

# Single architecture
python scripts/run_benchmark.py --arch rag
python scripts/run_benchmark.py --arch agentic
python scripts/run_benchmark.py --arch hybrid

# Skip LLM judge (faster, no extra API cost)
python scripts/run_benchmark.py --no-judge

# Generate report from latest results
python scripts/generate_report.py
```

Results are written to `results/run_<timestamp>.json`.
The report is written to `REPORT.md`.

## Metrics

| Metric | Source | Description |
|---|---|---|
| Topic coverage | heuristic | % of expected topics found in the answer |
| Ticker recall | heuristic | % of expected stock tickers mentioned |
| Citation count | heuristic | Number of `[doc_N]` citations in the answer |
| Judge score (/5) | LLM | Avg of completeness, groundedness, citation quality, conciseness |
| Retrieval time | timing | Seconds for search + rerank (RAG / hybrid only) |
| Total time | timing | Wall-clock seconds per query |
| Input / output tokens | API | Raw token counts from Anthropic usage |
| Cache read tokens | API | Tokens served from prompt cache |
| Cost | computed | USD cost per query at Haiku 4.5 pricing |

## Project Structure

```
rag-benchmark/
├── config.py              # All tunable parameters
├── requirements.txt
├── architectures/
│   ├── base.py            # BenchmarkRun dataclass + abstract BaseArchitecture
│   ├── pure_rag.py
│   ├── pure_agentic.py
│   └── hybrid.py
├── benchmark/
│   ├── eval_set.json      # 10 financial queries with expected topics/tickers
│   ├── runner.py          # Orchestrates runs, calls judge, saves JSON
│   └── judge.py           # LLM-as-judge (Haiku 4.5)
├── shared/
│   ├── chunker.py         # Structural markdown chunker
│   ├── search.py          # HybridSearcher + Reranker
│   ├── corpus_tools.py    # CorpusIndex + tool schemas for agentic architectures
│   └── llm.py             # Anthropic client wrapper (caching + cost tracking)
├── results/               # run_<timestamp>.json files
├── scripts/
│   ├── run_benchmark.py
│   └── generate_report.py
└── REPORT.md              # Auto-generated comparison report
```

## Prompt Caching

The system prompt is cached on every LLM call via `cache_control: {type: "ephemeral"}`.
In the agentic loop, the cache persists across all iterations of a single query,
so only the first iteration pays the cache-write cost (~1.25× input price).
Subsequent iterations read at ~0.1× input price.
