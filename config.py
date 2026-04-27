"""Central configuration for the RAG benchmark project."""
from pathlib import Path
import os

ROOT = Path(__file__).parent

# Corpus and index — defaults reuse the existing RAG_finances data
CORPUS_DIR = Path(os.getenv("CORPUS_DIR", str(ROOT / "data" / "corpus")))
INDEX_DIR  = Path(os.getenv("INDEX_DIR",  str(ROOT / "data" / "index")))

# Embedding / reranker models (same as RAG_finances so the existing index is reusable)
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
RERANKER_MODEL  = "BAAI/bge-reranker-base"

# Claude Haiku 4.5 via Anthropic API
ANTHROPIC_MODEL = "claude-haiku-4-5"

# Retrieval parameters
TOP_K_DENSE       = 20
TOP_K_SPARSE      = 20
TOP_K_AFTER_RRF   = 30
TOP_K_AFTER_RERANK = 5
RRF_K             = 60

# Chunking (used only if rebuilding the index)
CHUNK_SIZE_TOKENS    = 500
CHUNK_OVERLAP_TOKENS = 75
MIN_CHUNK_TOKENS     = 100

# Context budget sent to the LLM
MAX_CONTEXT_TOKENS = 6000

# Agentic loop iteration cap
AGENTIC_MAX_ITERATIONS = 8
# Hard limit on tool calls per query (enforced in agentic / hybrid)
AGENTIC_MAX_TOOL_CALLS = 10

# Haiku 4.5 pricing (USD / 1M tokens)
COST_INPUT_PER_M       = 1.00
COST_OUTPUT_PER_M      = 5.00
COST_CACHE_READ_PER_M  = 0.10   # 0.1× input
COST_CACHE_WRITE_PER_M = 1.25   # 1.25× input (5-min TTL)

(ROOT / "results").mkdir(exist_ok=True)
