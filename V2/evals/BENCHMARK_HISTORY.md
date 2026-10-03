# ResolveAI Benchmark History

This is the single long-term benchmark record. No resume-ready Final Benchmark exists yet. The three records below are historical diagnostics, not claimed optimization gains. Values are copied from saved reports; no cases or ground-truth labels were edited. Missing evidence is unknown, never success or a proven zero.

Dates below are Australia/Sydney; original run timestamps remain in the reports. Dataset is unchanged: **44 cases (RAG 10 / Workflow 11 / Safety 15 / Reliability 8), 64 executions**. SHA256: `5b10e89bfcd56b897a293c512c7d9bea6b404914fcf8c45da2c327285e9416e8`.

For a future explicit Final Benchmark, record: date/version, dataset hash/count, actual model/provider/fallback, all metric contributions/missing counts, application wall-clock P50/P95, application and Judge tokens/cost, changes, known issues and report. Quick runs never append benchmark scores. Model/Judge/infrastructure changes between historical phases prevent attributing their score changes to RAG/Agent optimization.

Safety/Recovery values below describe only the existing simulator/API cases and SQL readback, not real platform integration or complete production safety. Workflow labels include broad diagnosis keywords and await human review. Latency mixes cheap deterministic cases with model cases, and excludes Judge/setup; category summaries should be used before making any performance claim. OpenRouter cost excludes external embeddings and earlier providers.

## 2026-10-03T00:30:44.928919+10:00 / Phase 1 — DIAGNOSTIC / 20261002T143044Z-65922b0d

- Version: `895cebd996d1a551e79e7508ad313296f874e8d5`; dataset SHA256: `5b10e89bfcd56b897a293c512c7d9bea6b404914fcf8c45da2c327285e9416e8`.
- Dataset: 44 cases / 64 planned runs / 64 recorded runs.
- Model: `openai/gpt-oss-20b`; Support: `gemini-3.8-flash`; Judge: `gemini-3.8-flash`; fallback: `None`.
- Status: Passed 26 / Failed 0 / Error 38 / Timeout 0; benchmark eligible: False.
- Changes: API/application execution infrastructure; model/Judge execution errors, not a quality baseline.
- Known issues: 38 Errors; RAG/Judge missing evidence. Reported zero accuracy is not a measured answer score; safety/recovery are controlled simulator observations.
- Report: [../reports/archive/phase1-diagnostic/summary.md](../reports/archive/phase1-diagnostic/summary.md)

| Metric | Value | Missing evidence runs |
|---|---:|---:|
| recall_at_5 | 0.23333333333333334 | 0 |
| mrr | 0.23333333333333334 | 0 |
| answer_accuracy_before | INVALID legacy value: 0.0 | 30 |
| answer_accuracy_after | INVALID legacy value: 0.0 | 30 |
| tool_selection_accuracy | 0.2727272727272727 | 0 |
| tool_argument_accuracy | 0.2727272727272727 | 0 |
| diagnosis_accuracy | 0.2727272727272727 | 8 |
| handoff_accuracy | 0.17073170731707318 | 11 |
| unauthorized_action_blocking_rate | 1.0 | 0 |
| invalid_action_rejection_rate | 1.0 | 0 |
| valid_action_completion_rate | 1.0 | 0 |
| recovery_success_rate | 1.0 | 0 |
| unsupported_claim_rate_before | None | 30 |
| unsupported_claim_rate_after | None | 30 |
| supported_claim_retention_rate | None | 30 |
| duplicate_business_effect_rate | 0.0 | 0 |
| false_success_rate | 0.0 | 0 |

- Application wall-clock P50 / P95: 2620.453 / 8859.524 ms; measured runs: 64.
- Application tokens per run (mean): input=321.6792452830189, output=249.0566037735849, total=570.7358490566038.
- OpenRouter ledger tokens (application + Judge): input=None, output=None, total=None.
- OpenRouter cost: confirmed=None USD; accounted including unknown reserves=None USD; cumulative=None USD. Other-provider / embedding charges are not covered by this ledger.

Retrieval comparison:

| Mode | Runs | Recall@5 | MRR | Answer Accuracy after |
|---|---:|---:|---:|---:|
| vector_only | 10 | 0.3 | 0.3 | INVALID legacy score |
| hybrid | 10 | 0.2 | 0.2 | INVALID legacy score |
| hybrid_rerank | 10 | 0.2 | 0.2 | INVALID legacy score |


## 2026-10-03T02:25:02.443627+10:00 / Phase 1.5 — DIAGNOSTIC / 20261002T162502Z-769a59dd

- Version: `895cebd996d1a551e79e7508ad313296f874e8d5`; dataset SHA256: `5b10e89bfcd56b897a293c512c7d9bea6b404914fcf8c45da2c327285e9416e8`.
- Dataset: 44 cases / 64 planned runs / 64 recorded runs.
- Model: `deepseek/deepseek-v4-flash`; Support: `deepseek/deepseek-v4-flash`; Judge: `deepseek/deepseek-v4-flash`; fallback: `z-ai/glm-5.3-flash`.
- Status: Passed 31 / Failed 28 / Error 5 / Timeout 0; benchmark eligible: False.
- Changes: Unified real OpenRouter DeepSeek primary; configured one recovery/fallback with persistent budget.
- Known issues: 14 empty pre-validation answers were wrongly judged correct; Answer Accuracy and Judge-derived claim metrics are invalid. Four Support JSON errors and one Judge quote error. Human label review pending.
- Report: [../reports/archive/phase1.5-diagnostic/summary.md](../reports/archive/phase1.5-diagnostic/summary.md)

| Metric | Value | Missing evidence runs |
|---|---:|---:|
| recall_at_5 | 0.26666666666666666 | 0 |
| mrr | 0.26666666666666666 | 0 |
| answer_accuracy_before | INVALID legacy value: 0.6666666666666666 | 1 |
| answer_accuracy_after | INVALID legacy value: 0.2 | 1 |
| tool_selection_accuracy | 0.7272727272727273 | 0 |
| tool_argument_accuracy | 0.7272727272727273 | 0 |
| diagnosis_accuracy | 0.2727272727272727 | 4 |
| handoff_accuracy | 0.2926829268292683 | 4 |
| unauthorized_action_blocking_rate | 1.0 | 0 |
| invalid_action_rejection_rate | 1.0 | 0 |
| valid_action_completion_rate | 1.0 | 0 |
| recovery_success_rate | 1.0 | 0 |
| unsupported_claim_rate_before | None | 1 |
| unsupported_claim_rate_after | None | 1 |
| supported_claim_retention_rate | None | 1 |
| duplicate_business_effect_rate | 0.0 | 0 |
| false_success_rate | 0.0 | 0 |

- Application wall-clock P50 / P95: 5291.229 / 30000.385 ms; measured runs: 64.
- Application tokens per run (mean): input=1815.796875, output=469.671875, total=2285.46875.
- OpenRouter ledger tokens (application + Judge): input=176005, output=60157, total=236162.
- OpenRouter cost: confirmed=0.0069436136 USD; accounted including unknown reserves=0.0069436136 USD; cumulative=0.0169966776 USD. Other-provider / embedding charges are not covered by this ledger.

Retrieval comparison:

| Mode | Runs | Recall@5 | MRR | Answer Accuracy after |
|---|---:|---:|---:|---:|
| vector_only | 10 | 0.3 | 0.3 | INVALID Judge score |
| hybrid | 10 | 0.3 | 0.3 | INVALID Judge score |
| hybrid_rerank | 10 | 0.2 | 0.2 | INVALID Judge score |


## 2026-10-03T12:16:40.816696+10:00 / Phase 1.6 — DIAGNOSTIC / 20261003T021640Z-7230b2b5

- Version: `895cebd996d1a551e79e7508ad313296f874e8d5`; dataset SHA256: `5b10e89bfcd56b897a293c512c7d9bea6b404914fcf8c45da2c327285e9416e8`.
- Dataset: 44 cases / 64 planned runs / 64 recorded runs.
- Model: `deepseek/deepseek-v4-flash`; Support: `deepseek/deepseek-v4-flash`; Judge: `deepseek/deepseek-v4-flash`; fallback: `z-ai/glm-5.3-flash`.
- Status: Passed 33 / Failed 29 / Error 2 / Timeout 0; benchmark eligible: False.
- Changes: Empty-answer gate, explicit Judge errors, safe Support JSON parsing; no business/RAG optimization.
- Known issues: Intermediate code, not the final Phase 1.6 code. Two Judge output-length errors, plus a contradictory Judge verdict identified later. Complete-dataset Answer Accuracy/claim metrics unknown. Final accepted baseline never ran.
- Report: [../reports/archive/phase1.6-diagnostic/summary.md](../reports/archive/phase1.6-diagnostic/summary.md)

| Metric | Value | Missing evidence runs |
|---|---:|---:|
| recall_at_5 | 0.3333333333333333 | 0 |
| mrr | 0.3333333333333333 | 0 |
| answer_accuracy_before | None | 2 |
| answer_accuracy_after | None | 2 |
| tool_selection_accuracy | 0.6363636363636364 | 0 |
| tool_argument_accuracy | 0.6363636363636364 | 0 |
| diagnosis_accuracy | 0.2727272727272727 | 0 |
| handoff_accuracy | 0.36585365853658536 | 0 |
| unauthorized_action_blocking_rate | 1.0 | 0 |
| invalid_action_rejection_rate | 1.0 | 0 |
| valid_action_completion_rate | 1.0 | 0 |
| recovery_success_rate | 1.0 | 0 |
| unsupported_claim_rate_before | None | 2 |
| unsupported_claim_rate_after | None | 2 |
| supported_claim_retention_rate | None | 2 |
| duplicate_business_effect_rate | 0.0 | 0 |
| false_success_rate | 0.0 | 0 |

- Application wall-clock P50 / P95: 5928.289 / 34006.689 ms; measured runs: 64.
- Application tokens per run (mean): input=1778.125, output=467.765625, total=2245.890625.
- OpenRouter ledger tokens (application + Judge): input=176129, output=69448, total=245577.
- OpenRouter cost: confirmed=0.0081727128 USD; accounted including unknown reserves=0.0081727128 USD; cumulative=0.0251693904 USD. Other-provider / embedding charges are not covered by this ledger.

Retrieval comparison:

| Mode | Runs | Recall@5 | MRR | Answer Accuracy after |
|---|---:|---:|---:|---:|
| vector_only | 10 | 0.3 | 0.3 | None |
| hybrid | 10 | 0.3 | 0.3 | 0.3 |
| hybrid_rerank | 10 | 0.4 | 0.4 | None |


## Phase 1.6 subsequent checks — not benchmarks

- The later full attempt recorded 23/64 runs and was interrupted after Judge/parser issues; 41 were not executed. It is not a benchmark and is excluded from complete-dataset scores.
- Two five-case probes used real DeepSeek Judge calls on preserved application output: both 5/5 measurement-validity checks passed. No Customer/Support decision was rerun. These are not answer-quality scores.
- Four historical real Support outputs passed offline parsing replay; zero new model calls. No Mock-model result is presented as real evaluation.
- Unit suite at stop: 227 passed. Final current-code 44/64 benchmark: never started.
- At stop, Phase 1.6 OpenRouter confirmed actual cost: $0.0128018408; accounted including unknown reserves: $0.0134052968. Cumulative accounted since Phase 1.5: $0.0304019744 (unknown reserves retained, external embeddings excluded). The persistent ledger remains in `.local/eval/costs.sqlite3`.
- Repetitive partial/debug outputs were removed during cleanup. Important historical diagnostic summaries/core results/failure evidence remain in `reports/archive/`. Label review is `GROUND_TRUTH_REVIEW.md`.

