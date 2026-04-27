"""Hybrid search (dense + BM25 + RRF) and cross-encoder reranker.

Ported from RAG_finances retrieval/hybrid_search.py + retrieval/reranker.py.
Reads the existing index from config.INDEX_DIR (defaults to RAG_finances data).
"""
import pickle
import re
from dataclasses import dataclass
from typing import Optional

import numpy as np
from qdrant_client import QdrantClient
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer, CrossEncoder

import config

COLLECTION_NAME = "finance_chunks"
BM25_PATH   = config.INDEX_DIR / "bm25.pkl"
CHUNKS_PATH = config.INDEX_DIR / "chunks.pkl"


def simple_tokenize(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


@dataclass
class SearchResult:
    chunk_id: str
    doc_id: str
    doc_title: str
    section_path: str
    raw_text: str
    date: str
    tickers: list
    score: float
    dense_rank: Optional[int] = None
    sparse_rank: Optional[int] = None


class HybridSearcher:
    def __init__(self):
        for path in (BM25_PATH, CHUNKS_PATH):
            if not path.exists():
                raise FileNotFoundError(
                    f"Index file not found: {path}\n"
                    f"Run 'python -m ingestion.indexer' from the RAG_finances directory first."
                )
        self.model = SentenceTransformer(config.EMBEDDING_MODEL)
        self.qdrant = QdrantClient(path=str(config.INDEX_DIR / "qdrant_db"))
        with open(BM25_PATH, "rb") as f:
            self.bm25: BM25Okapi = pickle.load(f)
        with open(CHUNKS_PATH, "rb") as f:
            self.chunks: list[dict] = pickle.load(f)
        self.chunks_by_id = {c["chunk_id"]: c for c in self.chunks}
        self.chunk_ids_ordered = [c["chunk_id"] for c in self.chunks]

    def dense_search(self, query: str, top_k: int) -> list[tuple[str, float]]:
        emb = self.model.encode(query, normalize_embeddings=True, convert_to_numpy=True)
        results = self.qdrant.query_points(
            collection_name=COLLECTION_NAME,
            query=emb.tolist(),
            limit=top_k,
        ).points
        return [(str(r.id), r.score) for r in results]

    def sparse_search(self, query: str, top_k: int) -> list[tuple[str, float]]:
        tokens = simple_tokenize(query)
        scores = self.bm25.get_scores(tokens)
        top_idx = np.argsort(scores)[::-1][:top_k]
        return [(self.chunk_ids_ordered[i], float(scores[i]))
                for i in top_idx if scores[i] > 0]

    def _rrf(
        self,
        dense: list[tuple[str, float]],
        sparse: list[tuple[str, float]],
    ) -> list[tuple[str, float, Optional[int], Optional[int]]]:
        k = config.RRF_K
        scores: dict[str, float] = {}
        d_ranks: dict[str, int] = {}
        s_ranks: dict[str, int] = {}
        for rank, (cid, _) in enumerate(dense, 1):
            scores[cid] = scores.get(cid, 0) + 1.0 / (k + rank)
            d_ranks[cid] = rank
        for rank, (cid, _) in enumerate(sparse, 1):
            scores[cid] = scores.get(cid, 0) + 1.0 / (k + rank)
            s_ranks[cid] = rank
        ordered = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [(cid, sc, d_ranks.get(cid), s_ranks.get(cid)) for cid, sc in ordered]

    def search(self, query: str, top_k: int | None = None) -> list[SearchResult]:
        top_k = top_k or config.TOP_K_AFTER_RRF
        dense  = self.dense_search(query, config.TOP_K_DENSE)
        sparse = self.sparse_search(query, config.TOP_K_SPARSE)
        fused  = self._rrf(dense, sparse)
        results: list[SearchResult] = []
        for cid, score, d_rank, s_rank in fused:
            chunk = self.chunks_by_id.get(cid)
            if not chunk:
                continue
            results.append(SearchResult(
                chunk_id=cid,
                doc_id=chunk["doc_id"],
                doc_title=chunk.get("doc_title", ""),
                section_path=chunk.get("section_path", ""),
                raw_text=chunk.get("raw_text", ""),
                date=chunk.get("date", ""),
                tickers=chunk.get("tickers", []),
                score=score,
                dense_rank=d_rank,
                sparse_rank=s_rank,
            ))
            if len(results) >= top_k:
                break
        return results


class Reranker:
    def __init__(self):
        self.model = CrossEncoder(config.RERANKER_MODEL)

    def rerank(
        self,
        query: str,
        results: list[SearchResult],
        top_k: int | None = None,
    ) -> list[SearchResult]:
        if not results:
            return []
        top_k = top_k or config.TOP_K_AFTER_RERANK
        pairs = [(query, r.raw_text) for r in results]
        scores = self.model.predict(pairs, show_progress_bar=False)
        for r, sc in zip(results, scores):
            r.score = float(sc)
        return sorted(results, key=lambda x: x.score, reverse=True)[:top_k]
