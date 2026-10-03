# ResolveAI Evaluation

Only two evaluation modes, one entry point. `smoke.jsonl` has 83 human-authored cases: RAG 10, Workflow 50, Safety 15, Reliability 8. Workflow is frozen at 40 Development/Regression (17/23) and 10 entirely new Holdout cases; all 34 previously exposed cases remain available for optimization. See `WORKFLOW_EVAL_SPEC.md` and `GROUND_TRUTH_REVIEW.md`. Holdout is excluded from optimization; Final includes it and requires separate owner authorization. There is no automatic full evaluation or unit suite execution.

For Workflow data checks only, use `python -m evals.dataset --validate` (frozen hash/schema/splits) or `python -m evals.dataset --check-fixtures` (isolated local simulator, no Agent/LLM). The semantic `workflow_ground_truth` is authoritative; legacy keyword metrics do not implement the whole frozen contract.

## Authorized Workflow evaluation

Workflow uses the existing entry point and frozen splits, without running RAG, Safety, Reliability, Final Benchmark or LangSmith:

```powershell
$env:EVAL_MAX_REQUEST_BYTES = '49152'
.\.venv\Scripts\python -m evals.run --category workflow --workflow-stage baseline
.\.venv\Scripts\python -m evals.run --category workflow --cases flow-shipment flow-auth flow-stock
.\.venv\Scripts\python -m evals.run --category workflow --workflow-stage optimized
.\.venv\Scripts\python -m evals.run --category workflow --workflow-stage holdout
```

Baseline and optimized select all 40 Development/Regression cases. Holdout selects the 10 new cases once, requires a completed optimized report, and has a persistent start marker to prevent replay. Stage reports cannot be overwritten. Explicit Workflow case lists run at most 11 cases and preserve errors while continuing the selected checks; small default Quick runs still stop on the first error. Every execution stays in the denominator.

Semantic scoring checks every required fact and all final claims against independent simulator readback, legal action prerequisites, persisted plans/tickets and user restrictions. Tool routes and scoped arguments are deterministic; argument correctness is independent from tool selection. Optional unnecessary calls are assessed separately from system plan/verifier reads. One retry after an unavailable read is allowed by the frozen contract. Judge errors remain unknown. Application and judge share the configured routing policy; actual DeepSeek/GLM usage and costs are recorded rather than described as single-model results.

Only important milestones belong in `BENCHMARK_HISTORY.md`. Meaningful measured changes append simple Problem / Change / Before / After / Why entries to `OPTIMIZATION_HISTORY.md`. Long-term Workflow reports use `reports/workflow/baseline`, `optimized`, and `holdout`; targeted debugging overwrites `reports/latest`. Retain original outputs when correcting a scoring error; never present that correction as an Agent gain.

## Quick Evaluation

From the project root:

```powershell
.\.venv\Scripts\python -m evals.run --category workflow
.\.venv\Scripts\python -m evals.run --category rag --cases rag-sync rag-auth rag-stock --retrieval-modes hybrid
```

Always specify one category. Defaults select 4–5 representative existing cases for that category; `--cases` accepts at most 5. RAG uses one retrieval mode per Quick run, default `hybrid_rerank`. Other categories do not run RAG. Only test the module being changed; do not rerun unchanged Safety or Reliability.

Quick reuses its own `resolveai_eval_*` database and imported documents. Business fixtures reset per case; application tables are never reset. Local services start once per run and stop afterwards. The application and scoring are identical to Final; Quick is not a quality benchmark. It stops on the first Error/Timeout. Per-case timeout defaults to 180 seconds; the execution loop has a 25-minute wall-clock limit, with setup/service shutdown additional. Feedback target: 20–30 minutes, not a measured guarantee.

Run only the affected unit tests during development. Pure entry/path validation is free:

```powershell
.\.venv\Scripts\python -m evals.run --validate
```

`--validate` is a preflight check, not a third evaluation mode. It produces no scores and makes no external calls.

## Final Benchmark

Run only when the owner explicitly requests it, after optimization and targeted validation:

```powershell
.\.venv\Scripts\python -m evals.run --mode final --changes "Describe this measured change"
```

Uses a fresh isolated database. Runs all 83 cases, including the three RAG modes (`vector_only`, `hybrid`, `hybrid_rerank`): 103 executions. This includes the frozen Holdout and must never be used for optimization. No retry of the full benchmark. First Error/Timeout stops execution, and unexecuted cases are listed without invented results. Final archives its report and appends `BENCHMARK_HISTORY.md`, including invalid/incomplete runs explicitly marked ineligible. Application failures remain in denominators; missing Judge evidence remains unknown.

Model/provider/fallback settings remain in `.env`. The persistent `.local/eval/costs.sqlite3` ledger is never reset by cleanup; primary, Judge and fallback calls share the existing cumulative budget below $1. OpenRouter prices are checked when a real run starts. Unknown accepted calls keep their reserves. External embedding costs remain outside this OpenRouter ledger and must not be advertised as zero.

## Files and reports

- Entry/data/history: `run.py`, `dataset.py`, `smoke.jsonl`, `BENCHMARK_HISTORY.md`, `GROUND_TRUTH_REVIEW.md`.
- Execution/fixtures: `harness.py`, `execute.py`, `scenarios.py`, `runtime.py`, `proxy.py`.
- Scoring: `judge.py`, `metrics.py`.
- Recording/budget: `observe.py`, `artifacts.py`, `budget.py`.

Keep these modules flat; no parallel legacy evaluation entry points or Phase-specific scripts. Workflow labels are frozen after business-code review and fixture-only validation; suspected Ground Truth errors require human confirmation before any change. Historical RAG label snapshots remain outside this Workflow review.

```text
reports/
  latest/
    summary.md
    summary.json             # run configuration is included here
    evaluation_results.json  # core results for every executed case
    failures/                # full result JSON; nonempty failure logs only
  archive/
    <important benchmark>/   # same structure
```

Quick replaces `latest`; only explicit Final runs are archived. Passed cases retain core metrics, token/cost/latency fields and an answer preview, not full snapshots or logs. Failed/Error/Timeout cases retain their full case result, including model observations, Judge audit and business readback; duplicate case files, observation files and HTTP dumps are not exported. Report tokens/credentials never belong in `reports/`: all working files and credentials use gitignored `.local/eval/`. Completed run temporary files are removed. Abruptly interrupted runs may remain there for diagnosis.

Metrics: Recall@5, MRR, Answer Accuracy before/after claim validation, Unsupported Claim Rate before/after, Supported Claim Retention Rate, Tool Selection/Argument Accuracy, Diagnosis/Handoff Accuracy, Unauthorized Blocking, Invalid Rejection, Valid Completion, Recovery Success, Duplicate Business Effect, False Success, application P50/P95, model/tool calls and token usage. Cost is separately scoped to OpenRouter. Read missing-evidence counts and benchmark eligibility before using a value.

`BENCHMARK_HISTORY.md` is the long-term record. Historical diagnostic metrics are not resume benchmarks. Pin the dataset/model/scoring when comparing optimization, and obtain a valid pre-optimization baseline before claiming gains.
