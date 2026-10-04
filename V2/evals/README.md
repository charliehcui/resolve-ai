# ResolveAI Evaluation

Python modules remain in the `evals/` root. The directory layout separates records, specifications, datasets and RAG scoring data.

| Path | Purpose |
|---|---|
| `optimization/` | Module optimization records: [Workflow](optimization/workflow.md) and [RAG](optimization/rag.md). |
| `specs/` | Evaluation standards and Ground Truth: [Workflow](specs/WORKFLOW_EVAL_SPEC.md), [Workflow splits](specs/WORKFLOW_SPLIT.md), [RAG](specs/RAG_EVAL_SPEC.md) and [Ground Truth review](specs/GROUND_TRUTH_REVIEW.md). |
| `data/` | Evaluation dataset: [smoke.jsonl](data/smoke.jsonl). |
| `rag/` | RAG-specific scoring data: [answer-rubric.json](rag/answer-rubric.json). |
| [BENCHMARK_HISTORY.md](BENCHMARK_HISTORY.md) | Formal Benchmark checkpoints; retain historical results and original evidence. |
| Root Python files | Evaluation execution, judging, metrics, recording, budgets and runtime infrastructure. |

`run.py` is the public entry point. `dataset.py` loads and validates the frozen dataset; `harness.py`, `execute.py`, `scenarios.py`, `runtime.py` and `proxy.py` run cases and fixtures. `judge.py` and `metrics.py` score results; `observe.py`, `artifacts.py` and `budget.py` record evidence and costs. `rag.py` supports RAG reports and retrieval comparisons.

From the V2 project root, validate local files without model calls or evaluation execution:

```powershell
.\.venv\Scripts\python -m evals.run --validate
.\.venv\Scripts\python -m evals.dataset --validate
.\.venv\Scripts\python -m evals.dataset --validate-rag
```

Quick Evaluation provides feedback for one selected module. Final Benchmark includes Holdout and requires an explicit owner request. Holdout is excluded from optimization; never change frozen Dataset / Ground Truth or scoring rules to improve results. Run only the unit tests relevant to a change.

Stage reports remain in `reports/workflow/` and `reports/rag/`. Quick reports use `reports/latest/`; Final archives important runs under `reports/archive/`. Corrected scoring must preserve the original result and must not be presented as an Agent improvement.
