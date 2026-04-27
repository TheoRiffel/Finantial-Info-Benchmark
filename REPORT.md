# Benchmark Report

**Configs**: `runs`  

---

## Summary Table

| Architecture | Config | Faithfulness | Correctness | Latency p50 (s) | Cost / query ($) |
| --- | --- | --- | --- | --- | --- |
| `Pure Agentic` | runs | 0.525 | 0.344 | 8.46 | $0.03385 |
| `Hybrid` | runs | 0.643 | 0.663 | 11.22 | $0.03127 |
| `Pure RAG` | runs | 0.691 | 0.584 | 4.48 | $0.00435 |

---

## Plots

![bar_quality](results/plots/bar_quality.png)

![scatter_cost_quality](results/plots/scatter_cost_quality.png)

![heatmap_category](results/plots/heatmap_category.png)


---

## Top 3 Failures per Architecture

*(run without `--no-failures` to see LLM-categorized failures)*
