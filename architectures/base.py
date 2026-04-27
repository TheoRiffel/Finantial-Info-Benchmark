"""Abstract base class and result dataclass for all benchmark architectures."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class BenchmarkRun:
    answer: str
    # chunk_ids returned by vector/BM25 retrieval (RAG / hybrid)
    retrieved_ids: list[str] = field(default_factory=list)
    # filenames the agent actually read via tools (agentic / hybrid)
    accessed_ids: list[str] = field(default_factory=list)
    # wall-clock seconds per phase
    latency: dict[str, float] = field(default_factory=lambda: {
        "retrieval": 0.0, "generation": 0.0, "total": 0.0
    })
    # raw token counts
    tokens: dict[str, int] = field(default_factory=lambda: {
        "input": 0, "output": 0, "cache_read": 0, "cache_write": 0
    })
    cost_usd: float = 0.0
    tool_calls: int = 0
    error: str = ""
    metadata: dict = field(default_factory=dict)


class BaseArchitecture(ABC):
    name: str = "base"

    def load(self) -> None:
        """Load models/indices once before benchmarking."""

    @abstractmethod
    def run_query(self, question: str) -> BenchmarkRun:
        ...
