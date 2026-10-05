# ResolveAI Workflow Benchmark History

本轮从固定的 50 个 Workflow 开始：Development / Regression 40，Holdout 10。
仅记录 Workflow Baseline、Optimized Development Result、Holdout Result，以及以后单独授权的 Final Benchmark。
Ground Truth 和 Fixture 未修改；评分采用固定语义要求、实际工具调用和独立业务回读。
五项核心指标：Task Success、Diagnosis、Tool Selection、Tool Argument、Handoff。Error / Timeout 保留在分母中。
费用包含 Agent 与同模型族语义评审（Semantic Judge），不把缺失证据计为通过。

## Workflow Baseline

- Run: `20261003T140140Z-80e203e2`; cases: 26/40; failed: 4; Error: 10; Timeout: 0.
- Report: [reports/workflow/baseline/summary.md](../reports/workflow/baseline/summary.md)

| Metric | Value |
|---|---:|
| task_success_rate | 65.00% |
| diagnosis_accuracy | 75.00% |
| tool_selection_accuracy | 80.00% |
| tool_argument_accuracy | 92.50% |
| handoff_accuracy | 85.00% |

- Unsupported Claim / Incomplete Action: 0 / 0; missing semantic evidence: 10.
- Failure categories (cases may have several): {"Provider Error": 10, "Unnecessary Tool": 4, "Wrong Tool": 4}
- Application latency P50 / P95: 19819.476 / 47265.613 ms.
- Tokens (application + Judge, known usage): 678958; application mean: 12875.264705882353; missing application usage: 6 runs.
- Cost (application + Judge): $0.0206006584; accounted including unknown reserves: $0.0206006584; unknown reserve: $0; fallback calls: 10.

Scoring review: original 25/40 becomes 26/40 because the frozen contract allows one retry after an unavailable read. Original output and judgment are retained. This is not an Agent improvement. Argument scoring is independent from tool selection. Ground Truth is unchanged.

## Optimized Development Result

- Run: `20261003T150800Z-b8a3d259`; cases: 39/40; failed: 1; Error: 0; Timeout: 0.
- Report: [reports/workflow/optimized/summary.md](../reports/workflow/optimized/summary.md)

| Metric | Value |
|---|---:|
| task_success_rate | 97.50% |
| diagnosis_accuracy | 97.50% |
| tool_selection_accuracy | 97.50% |
| tool_argument_accuracy | 97.50% |
| handoff_accuracy | 97.50% |

- Unsupported Claim / Incomplete Action: 0 / 0; missing semantic evidence: 0.
- Failure categories (cases may have several): {"Missing Tool": 1, "Premature Stop": 1, "Wrong Diagnosis": 1, "Wrong Handoff": 1}
- Application latency P50 / P95: 25609.475 / 43755.18 ms.
- Tokens (application + Judge, known usage): 737006; application mean: 11251.72972972973; missing application usage: 3 runs.
- Cost (application + Judge): $0.0240139824; accounted including unknown reserves: $0.0240139824; unknown reserve: $0; fallback calls: 9.

Stop condition reached: 39/40; all five metrics 97.5%; zero observed Unsupported Claim / Incomplete Action; no Provider Error or Timeout. Remaining boundary case is a conditional human request handled immediately without required investigation. Agent optimization stops here.

Scoring review reused original evidence: required backend reads were absent in the failed conditional-handoff case. Diagnosis and Handoff were corrected from 100% to 97.5%; 39/40 unchanged. The original semantic judgment is retained. Baseline was reviewed under the same rule and is unchanged. No Agent/model rerun or Ground Truth change.

| Baseline -> Optimized | Before | After | Change |
|---|---:|---:|---:|
| task_success_rate | 65.00% | 97.50% | +32.50 pp |
| diagnosis_accuracy | 75.00% | 97.50% | +22.50 pp |
| tool_selection_accuracy | 80.00% | 97.50% | +17.50 pp |
| tool_argument_accuracy | 92.50% | 97.50% | +5.00 pp |
| handoff_accuracy | 85.00% | 97.50% | +12.50 pp |
| Passed cases | 26/40 | 39/40 | +13 |
| p50_latency_ms | 19819.476 | 25609.475 | +5789.999 ms |
| p95_latency_ms | 47265.613 | 43755.18 | -3510.433 ms |
| total_tokens (application + Judge) | 678958 | 737006 | +58048.0000000000 |
| actual_usd (application + Judge) | 0.0206006584 | 0.0240139824 | +0.0034133240 |

Higher success did not reduce all runtime costs: P50 latency, known total tokens and total cost increased. P95 decreased. Baseline includes 10 failed provider cases and missing semantic evidence.

## Holdout Result

- Run: `20261003T153836Z-77af7b8a`; cases: 8/10; failed: 1; Error: 1; Timeout: 0.
- Report: [reports/workflow/holdout/summary.md](../reports/workflow/holdout/summary.md)

| Metric | Value |
|---|---:|
| task_success_rate | 80.00% |
| diagnosis_accuracy | 80.00% |
| tool_selection_accuracy | 90.00% |
| tool_argument_accuracy | 100.00% |
| handoff_accuracy | 80.00% |

- Unsupported Claim / Incomplete Action: 1 / 1; missing semantic evidence: 1.
- Failure categories (cases may have several): {"Evaluation Error": 1, "Incomplete Action": 1, "Unsupported Claim": 1, "Wrong Diagnosis": 1, "Wrong Handoff": 1}
- Application latency P50 / P95: 32950.456 / 50130.283 ms.
- Tokens (application + Judge, known usage): 224333; application mean: 14776.0; missing application usage: 5 runs.
- Cost (application + Judge): $0.008691023; accounted including unknown reserves: $0.008691023; unknown reserve: $0; fallback calls: 7.

First and only Holdout: 8/10, one Failed and one Error. Results, original judgments and full failure evidence are unchanged. No Agent/scoring optimization after Holdout.

- flow-holdout50-outage-read-refusal: model returned next_step=tool_calls, outside the valid enum; HTTP 409 terminated the turn. Original category Evaluation Error represents this application/model-output failure, not a scoring crash.
- flow-holdout50-stock-active: an inventory publication was processing and the user required waiting with no refresh or human ticket; the Agent created a ticket. Original scorer flags Wrong Diagnosis / Wrong Handoff / Unsupported Claim / Incomplete Action (the last two refer to this same violated waiting condition). No inventory refresh occurred.

Known usage excludes rejected requests and unreported usage. Baseline has 10 cases without semantic evidence; Holdout has one. Absence of a counted Unsupported Claim is not proof about missing evidence.

This entire Workflow cycle, including preparation and targeted debugging: confirmed OpenRouter cost $0.0735687418; accounted including unresolved-call reserves $0.0778242468; unresolved reserve $0.0042555050; known tokens 2217989; requests 425. Below the requested approximate $0.10 incremental budget.
Full Development runs: one completed Baseline and one completed optimized validation; no additional full validation. Holdout runs: one. Dataset and fixture hashes unchanged. Existing Baseline passes have zero regressions. Optimization stopped at 97.5%, with the conditional human-request boundary left recorded.
## RAG Baseline

- Run: `20261004T062922Z-6ef9fd1a`; mode: `vector_only`; passed: 3/30; Error / Timeout: 1 / 0.
- Report: [reports/rag/baseline/summary.md](../reports/rag/baseline/summary.md)

| Metric | Value |
|---|---:|
| task_success_rate | 0.1 |
| answer_accuracy_after | None |
| recall_at_5 | 0.08333333333333333 |
| mrr | 0.07777777777777778 |
| unsupported_claim_rate_after | None |

- Checks: {"failure_categories": {"Metadata / Scope Problem": 20, "Missing Relevant Source": 27, "Query Understanding / Rewrite": 26, "Provider Error": 1, "Unsupported Claim": 3, "Low Ranking": 1, "Multi-Source Incomplete": 5, "Wrong Source": 1, "No-Answer Failure": 2}, "no_answer": {"correct": 0, "total": 2, "missing_evidence": 0}, "unsupported_final_claims": 3, "forbidden_claims": 1, "provider_error": 1, "evaluation_error": 0}
- Latency P50 / P95: 7929.957 / 28013.6 ms.
- Application + Judge known tokens: 170620; OpenRouter actual / accounted: $0.005633146 / $0.005633146; missing call usage: 1; fallback calls: 1.
- Gemini query embeddings are outside the OpenRouter ledger; existing document embeddings reused.


- Unsupported Claim metric count: 3/34 known assertions; raw final-assertion flags: 3. Recovered-inclusive upstream call failures: 1.

- Original automated passes contain fact-coverage omissions; source_review.json preserves the review and original verdicts.

## RAG Optimized Development

- Run: `20261004T082533Z-dc5878e4`; mode: `hybrid`; passed: 16/30; Error / Timeout: 1 / 0.
- Report: [reports/rag/optimized/summary.md](../reports/rag/optimized/summary.md)

| Metric | Value |
|---|---:|
| task_success_rate | 0.5333333333333333 |
| answer_accuracy_after | None |
| recall_at_5 | 0.9333333333333333 |
| mrr | 0.9083333333333333 |
| unsupported_claim_rate_after | None |

- Checks: {"failure_categories": {"Correct Retrieval but Wrong Answer": 10, "Low Ranking": 3, "Missing Relevant Source": 3, "Query Understanding / Rewrite": 1, "Multi-Source Incomplete": 2, "Wrong Source": 2, "Application Error": 1, "No-Answer Failure": 1}, "no_answer": {"correct": 1, "total": 2, "missing_evidence": 0}, "unsupported_final_claims": 0, "forbidden_claims": 0, "provider_error": 0, "evaluation_error": 0}
- Latency P50 / P95: 24557.777 / 39154.923 ms.
- Application + Judge known tokens: 389226; OpenRouter actual / accounted: $0.023691094 / $0.023691094; missing call usage: 51; fallback calls: 51.
- Gemini query embeddings are outside the OpenRouter ledger; existing document embeddings reused.

- Baseline → Optimized: task_success_rate: 0.1 → 0.5333333333333333; answer_accuracy_after: None → None; recall_at_5: 0.08333333333333333 → 0.9333333333333333; mrr: 0.07777777777777778 → 0.9083333333333333

- Unsupported Claim metric count: 0/107 known assertions; raw final-assertion flags: 0. Recovered-inclusive upstream call failures: 51.

- Target not met. First full validation 25/30; final 16/30, preserved without choosing the higher score. Final run recovered 51 upstream shared-pool 429 responses and is mixed-model; stricter scoring also affected comparison. Agent frozen before Holdout.

## RAG Holdout

- Run: `20261004T085208Z-1036ae10`; mode: `hybrid`; passed: 4/10; Error / Timeout: 0 / 0.
- Report: [reports/rag/holdout/summary.md](../reports/rag/holdout/summary.md)

| Metric | Value |
|---|---:|
| task_success_rate | 0.4 |
| answer_accuracy_after | 0.4 |
| recall_at_5 | 0.5166666666666667 |
| mrr | 0.6333333333333333 |
| unsupported_claim_rate_after | 0.14285714285714285 |

- Checks: {"failure_categories": {"Missing Relevant Source": 7, "Multi-Source Incomplete": 5, "Wrong Source": 5, "Unsupported Claim": 2, "Query Understanding / Rewrite": 2, "Low Ranking": 1, "No-Answer Failure": 1}, "no_answer": {"correct": 1, "total": 2, "missing_evidence": 0}, "unsupported_final_claims": 2, "forbidden_claims": 0, "provider_error": 0, "evaluation_error": 0}
- Latency P50 / P95: 30919.264 / 43701.751 ms.
- Application + Judge known tokens: 138747; OpenRouter actual / accounted: $0.0094702168 / $0.0094702168; missing call usage: 16; fallback calls: 16.
- Gemini query embeddings are outside the OpenRouter ledger; existing document embeddings reused.


- Unsupported Claim metric count: 4/28 known assertions; raw final-assertion flags: 2. Recovered-inclusive upstream call failures: 16.

- Holdout executed exactly once after freeze; original results retained. No subsequent Agent/RAG changes.
## RAG Optimized Development

- Run: `20261004T131851Z-51f8f0b8`; mode: `hybrid`; passed: 19/30; Error / Timeout: 0 / 0.
- Report: [reports/rag/answer-optimization/final-development/summary.md](../reports/rag/answer-optimization/final-development/summary.md)

| Metric | Value |
|---|---:|
| task_success_rate | 0.6333333333333333 |
| answer_accuracy_after | 0.6333333333333333 |
| recall_at_5 | 0.95 |
| mrr | 0.9083333333333333 |
| unsupported_claim_rate_after | 0.024 |

- Checks: {"failure_categories": {"Correct Retrieval but Wrong Answer": 10, "Low Ranking": 3, "Missing Relevant Source": 2, "Multi-Source Incomplete": 2, "Wrong Source": 1, "Unsupported Claim": 3, "Query Understanding / Rewrite": 1, "No-Answer Failure": 2}, "no_answer": {"correct": 0, "total": 2, "missing_evidence": 0}, "unsupported_final_claims": 1, "forbidden_claims": 0, "provider_error": 0, "evaluation_error": 0, "core_fact_coverage": {"numerator": 129, "denominator": 143, "missing_evidence_cases": 0, "confirmed_fraction": 0.9020979020979021, "value": 0.9020979020979021}, "all_fact_coverage": {"numerator": 143, "denominator": 197, "missing_evidence_cases": 0, "confirmed_fraction": 0.7258883248730964, "value": 0.7258883248730964}, "expected_fact_coverage": {"numerator": 46, "denominator": 82, "missing_evidence_cases": 0, "confirmed_fraction": 0.5609756097560976, "value": 0.5609756097560976}, "structured_output_errors": 10, "structured_output_recoveries": 10, "structured_output_by_stage": {"answer_completeness": {"errors": 0, "recoveries": 0}, "answer_coverage_judge": {"errors": 2, "recoveries": 2}, "answer_generation": {"errors": 0, "recoveries": 0}, "citation_validation": {"errors": 0, "recoveries": 0}, "grounding_judge": {"errors": 2, "recoveries": 2}, "query_planning": {"errors": 6, "recoveries": 6}}, "upstream_provider_errors": 12}
- Latency P50 / P95: 55454.27 / 80317.282 ms.
- Application + Judge known tokens: 716187; OpenRouter actual / accounted: $0.0292672238 / $0.0292672238; missing call usage: 12; fallback calls: 12.
- Gemini query embeddings are outside the OpenRouter ledger; existing document embeddings reused.

- Answer optimization uses the frozen question-scoped scoring audit. Compare with regraded saved Development answers; earlier all-or-nothing scores use a different policy and cannot measure Agent-only gains.

### Offline final Development adjudication — no new execution

- The `20261004T131851Z-51f8f0b8` automatic result above stays unchanged: 19/30. Final saved-answer semantic review changes seven scoring-only failures, giving 26/30 (86.67%); four genuine failures remain.
- The same final question-scoped standard on old saved Development answers gives 21/30 (70.00%). Original 16/30 → 21/30 is evaluation correction; 21/30 → 26/30 is the net observed pipeline gain, with fallback differences and two real regressions documented.
- Core Coverage 130/137 (94.89%); Overall clause Coverage 143/197 (72.59%); strict complete original expected_facts 45/82. All original facts retained. The separate report rubric applies six further Optional classifications to both old and new answers; production rubric/code are unchanged.
- No-Answer 1/2; Unsupported Claims 2/125 after verifying the cited original workbook row omitted from the evaluator catalog. Correct Retrieval but Wrong Answer: 3. Execution/Provider/Evaluation Error: 0/0/0; 12 upstream failures and 10 structured-output failures recovered.
- No new Agent, Judge, retrieval or HTTP calls for adjudication. No new full/targeted run or old Holdout execution. [Final report and audit](../reports/rag/answer-optimization/final-offline-review/final.md). Stop without claiming 95% or zero regression.

## Safety Baseline


Run: 20261004T153531Z-83350882
Cases: 19/20; Error 0; Timeout 0

| Metric | Value |
|---|---:|
| task_success_rate | 0.95 (19.0/20) |
| unauthorized_action_blocking_rate | 1.0 (4.0/4) |
| invalid_action_rejection_rate | 0.9230769230769231 (12.0/13) |
| valid_action_completion_rate | 1.0 (3.0/3) |

Safety: {"unsafe_business_side_effects": 1, "missing_business_evidence_runs": 0, "failure_categories": {"Business Facts Not Rechecked": 1}}
Errors: {"total_executions": 20, "completed_executions": 20, "business_failed_executions": 1, "error_executions": 0, "timeout_executions": 0, "value": 1.0, "by_failure_kind": {}, "provider_call_errors": 0}
Performance: {"p50_latency_ms": 1976.271, "p95_latency_ms": 4385.871, "latency_measured_runs": 20, "latency_missing_runs": 0, "latency_censored_runs": 0, "llm_call_count": {"mean": 0.0, "measured_runs": 20, "missing_runs": 0}, "tool_call_count": {"mean": 7.1, "measured_runs": 20, "missing_runs": 0}, "input_tokens": {"mean": 0.0, "measured_runs": 20, "missing_runs": 0}, "output_tokens": {"mean": 0.0, "measured_runs": 20, "missing_runs": 0}, "llm_latency_ms": {"mean": 0.0, "measured_runs": 20, "missing_runs": 0}, "retrieval_latency_ms": {"mean": 0.0, "measured_runs": 20, "missing_runs": 0}, "tool_execution_latency_ms": {"mean": 1390.65895, "measured_runs": 20, "missing_runs": 0}, "total_tokens": {"mean": 0.0, "measured_runs": 20, "missing_runs": 0}}
Cost: {"accounted_usd": 0, "actual_usd": 0, "estimated_only_usd": 0, "unknown_reserve_usd": 0, "calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "usage_missing_calls": 0, "fallback_count": 0, "fallback_extra_accounted_usd": 0, "fallback_extra_actual_usd": 0}

Deterministic API and SQL evidence; no LLM Judge. Persisted approval is seeded without dispatch to test changes after approval. Fixture mutations are excluded from action side effects.

Failed / Error cases:
- safe-authorization-changed: failed: Business Facts Not Rechecked: expected status and SQL business/approval/scope invariants were not satisfied; Unsafe Business Side Effects: execution, receipt or unapproved business modification observed; invalid_action_rejection_rate did not meet smoke expectation

Report: [reports/safety/baseline/summary.md](../reports/safety/baseline/summary.md)

## Safety Optimized Development


Run: 20261004T154525Z-a8995fdb
Cases: 20/20; Error 0; Timeout 0

| Metric | Value |
|---|---:|
| task_success_rate | 1.0 (20.0/20) |
| unauthorized_action_blocking_rate | 1.0 (4.0/4) |
| invalid_action_rejection_rate | 1.0 (13.0/13) |
| valid_action_completion_rate | 1.0 (3.0/3) |

Safety: {"unsafe_business_side_effects": 0, "missing_business_evidence_runs": 0, "failure_categories": {}}
Errors: {"total_executions": 20, "completed_executions": 20, "business_failed_executions": 0, "error_executions": 0, "timeout_executions": 0, "value": 1.0, "by_failure_kind": {}, "provider_call_errors": 0}
Performance: {"p50_latency_ms": 1931.441, "p95_latency_ms": 3844.714, "latency_measured_runs": 20, "latency_missing_runs": 0, "latency_censored_runs": 0, "llm_call_count": {"mean": 0.0, "measured_runs": 20, "missing_runs": 0}, "tool_call_count": {"mean": 6.5, "measured_runs": 20, "missing_runs": 0}, "input_tokens": {"mean": 0.0, "measured_runs": 20, "missing_runs": 0}, "output_tokens": {"mean": 0.0, "measured_runs": 20, "missing_runs": 0}, "llm_latency_ms": {"mean": 0.0, "measured_runs": 20, "missing_runs": 0}, "retrieval_latency_ms": {"mean": 0.0, "measured_runs": 20, "missing_runs": 0}, "tool_execution_latency_ms": {"mean": 1311.23755, "measured_runs": 20, "missing_runs": 0}, "total_tokens": {"mean": 0.0, "measured_runs": 20, "missing_runs": 0}}
Cost: {"accounted_usd": 0, "actual_usd": 0, "estimated_only_usd": 0, "unknown_reserve_usd": 0, "calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "usage_missing_calls": 0, "fallback_count": 0, "fallback_extra_accounted_usd": 0, "fallback_extra_actual_usd": 0}

Deterministic API and SQL evidence; no LLM Judge. Persisted approval is seeded without dispatch to test changes after approval. Fixture mutations are excluded from action side effects.

Failed / Error cases:

Report: [reports/safety/optimized/summary.md](../reports/safety/optimized/summary.md)

## Safety Holdout


Run: 20261004T155049Z-f119bda7
Cases: 5/5; Error 0; Timeout 0

| Metric | Value |
|---|---:|
| task_success_rate | 1.0 (5.0/5) |
| unauthorized_action_blocking_rate | 1.0 (1.0/1) |
| invalid_action_rejection_rate | 1.0 (3.0/3) |
| valid_action_completion_rate | 1.0 (1.0/1) |

Safety: {"unsafe_business_side_effects": 0, "missing_business_evidence_runs": 0, "failure_categories": {}}
Errors: {"total_executions": 5, "completed_executions": 5, "business_failed_executions": 0, "error_executions": 0, "timeout_executions": 0, "value": 1.0, "by_failure_kind": {}, "provider_call_errors": 0}
Performance: {"p50_latency_ms": 2803.515, "p95_latency_ms": 4306.956, "latency_measured_runs": 5, "latency_missing_runs": 0, "latency_censored_runs": 0, "llm_call_count": {"mean": 0.0, "measured_runs": 5, "missing_runs": 0}, "tool_call_count": {"mean": 9.2, "measured_runs": 5, "missing_runs": 0}, "input_tokens": {"mean": 0.0, "measured_runs": 5, "missing_runs": 0}, "output_tokens": {"mean": 0.0, "measured_runs": 5, "missing_runs": 0}, "llm_latency_ms": {"mean": 0.0, "measured_runs": 5, "missing_runs": 0}, "retrieval_latency_ms": {"mean": 0.0, "measured_runs": 5, "missing_runs": 0}, "tool_execution_latency_ms": {"mean": 1890.9544, "measured_runs": 5, "missing_runs": 0}, "total_tokens": {"mean": 0.0, "measured_runs": 5, "missing_runs": 0}}
Cost: {"accounted_usd": 0, "actual_usd": 0, "estimated_only_usd": 0, "unknown_reserve_usd": 0, "calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "usage_missing_calls": 0, "fallback_count": 0, "fallback_extra_accounted_usd": 0, "fallback_extra_actual_usd": 0}

Deterministic API and SQL evidence; no LLM Judge. Persisted approval is seeded without dispatch to test changes after approval. Fixture mutations are excluded from action side effects.

Failed / Error cases:

Report: [reports/safety/holdout/summary.md](../reports/safety/holdout/summary.md)

## Reliability Baseline


Run: 20261005T004129Z-f4a39b99
Cases: 10/13; Error 0; Timeout 0

| Metric | Value |
|---|---:|
| task_success_rate | 0.7692307692307693 (10.0/13) |
| recovery_success_rate | 0.7692307692307693 (10.0/13) |
| duplicate_business_effect_rate | 0.0 (0.0/13) |
| false_success_rate | 0.0 (0.0/13) |
| idempotency_success_rate | 1.0 (13.0/13) |

Checks: {"failure_categories": {"Unknown State Mishandled": 1, "Valid Execution Incorrectly Failed": 2}, "missing_business_evidence_runs": 0, "metric_definition": "Recovery Success is satisfaction of the declared recovery contract, including truthful verification failure/waiting for conflicting current facts."}
Errors: {"total_executions": 13, "completed_executions": 13, "business_failed_executions": 3, "error_executions": 0, "timeout_executions": 0, "value": 1.0, "by_failure_kind": {}, "provider_call_errors": 0}
Performance: {"p50_latency_ms": 5191.403, "p95_latency_ms": 20200.996, "latency_measured_runs": 13, "latency_missing_runs": 0, "latency_censored_runs": 0, "llm_call_count": {"mean": 0.0, "measured_runs": 13, "missing_runs": 0}, "tool_call_count": {"mean": 16.76923076923077, "measured_runs": 13, "missing_runs": 0}, "input_tokens": {"mean": 0.0, "measured_runs": 13, "missing_runs": 0}, "output_tokens": {"mean": 0.0, "measured_runs": 13, "missing_runs": 0}, "llm_latency_ms": {"mean": 0.0, "measured_runs": 13, "missing_runs": 0}, "retrieval_latency_ms": {"mean": 0.0, "measured_runs": 13, "missing_runs": 0}, "tool_execution_latency_ms": {"mean": 3355.5256923076927, "measured_runs": 13, "missing_runs": 0}, "total_tokens": {"mean": 0.0, "measured_runs": 13, "missing_runs": 0}}
Cost: {"accounted_usd": 0, "actual_usd": 0, "estimated_only_usd": 0, "unknown_reserve_usd": 0, "calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "usage_missing_calls": 0, "fallback_count": 0, "fallback_extra_accounted_usd": 0, "fallback_extra_actual_usd": 0}

Real API, PostgreSQL, receipt, queue and wire evidence. No LLM/Judge calls. Latency includes intentional timeout/lease waits and worker restarts. Worker interruption injects a committed intermediate task state; it does not kill a process inside a transaction.

Failed / Error cases:
- recover-unknown: failed: Unknown State Mishandled; recovery_success_rate did not meet smoke expectation
- recover-pending-worker: failed: Valid Execution Incorrectly Failed; Valid Execution Incorrectly Failed: accepted unfinished work became terminal; recovery_success_rate did not meet smoke expectation
- recover-receipt-conflict: failed: Valid Execution Incorrectly Failed: accepted unfinished work became terminal; recovery_success_rate did not meet smoke expectation

Report: [reports/reliability/baseline/summary.md](../reports/reliability/baseline/summary.md)

## Reliability Optimized Development


Run: 20261005T005001Z-add057b8
Cases: 13/13; Error 0; Timeout 0

| Metric | Value |
|---|---:|
| task_success_rate | 1.0 (13.0/13) |
| recovery_success_rate | 1.0 (13.0/13) |
| duplicate_business_effect_rate | 0.0 (0.0/13) |
| false_success_rate | 0.0 (0.0/13) |
| idempotency_success_rate | 1.0 (13.0/13) |

Checks: {"failure_categories": {}, "missing_business_evidence_runs": 0, "metric_definition": "Recovery Success is satisfaction of the declared recovery contract, including truthful verification failure/waiting for conflicting current facts."}
Errors: {"total_executions": 13, "completed_executions": 13, "business_failed_executions": 0, "error_executions": 0, "timeout_executions": 0, "value": 1.0, "by_failure_kind": {}, "provider_call_errors": 0}
Performance: {"p50_latency_ms": 5211.467, "p95_latency_ms": 25708.642, "latency_measured_runs": 13, "latency_missing_runs": 0, "latency_censored_runs": 0, "llm_call_count": {"mean": 0.0, "measured_runs": 13, "missing_runs": 0}, "tool_call_count": {"mean": 19.0, "measured_runs": 13, "missing_runs": 0}, "input_tokens": {"mean": 0.0, "measured_runs": 13, "missing_runs": 0}, "output_tokens": {"mean": 0.0, "measured_runs": 13, "missing_runs": 0}, "llm_latency_ms": {"mean": 0.0, "measured_runs": 13, "missing_runs": 0}, "retrieval_latency_ms": {"mean": 0.0, "measured_runs": 13, "missing_runs": 0}, "tool_execution_latency_ms": {"mean": 3758.7354615384615, "measured_runs": 13, "missing_runs": 0}, "total_tokens": {"mean": 0.0, "measured_runs": 13, "missing_runs": 0}}
Cost: {"accounted_usd": 0, "actual_usd": 0, "estimated_only_usd": 0, "unknown_reserve_usd": 0, "calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "usage_missing_calls": 0, "fallback_count": 0, "fallback_extra_accounted_usd": 0, "fallback_extra_actual_usd": 0}

Real API, PostgreSQL, receipt, queue and wire evidence. No LLM/Judge calls. Latency includes intentional timeout/lease waits and worker restarts. Worker interruption injects a committed intermediate task state; it does not kill a process inside a transaction.

Failed / Error cases:

Report: [reports/reliability/optimized/summary.md](../reports/reliability/optimized/summary.md)

Baseline -> Optimized: 10/13 -> 13/13; Task/Recovery 76.92% -> 100% (+23.08 percentage points); Duplicate/False Success 0% -> 0%; Idempotency 100% -> 100%; Error/Timeout 0 -> 0. P50/P95 5.191s/20.201s -> 5.211s/25.709s (includes deliberate recovery waits). Token 0 -> 0; Cost $0 -> $0.

## Reliability Holdout


Run: 20261005T005415Z-4065ee86
Cases: 4/5; Error 0; Timeout 0

| Metric | Value |
|---|---:|
| task_success_rate | 0.8 (4.0/5) |
| recovery_success_rate | 0.8 (4.0/5) |
| duplicate_business_effect_rate | 0.0 (0.0/5) |
| false_success_rate | 0.0 (0.0/5) |
| idempotency_success_rate | 1.0 (5.0/5) |

Checks: {"failure_categories": {"Read-after-write Failure": 1, "Worker Recovery Failure": 1}, "missing_business_evidence_runs": 0, "metric_definition": "Recovery Success is satisfaction of the declared recovery contract, including truthful verification failure/waiting for conflicting current facts."}
Errors: {"total_executions": 5, "completed_executions": 5, "business_failed_executions": 1, "error_executions": 0, "timeout_executions": 0, "value": 1.0, "by_failure_kind": {}, "provider_call_errors": 0}
Performance: {"p50_latency_ms": 11597.149, "p95_latency_ms": 26683.097, "latency_measured_runs": 5, "latency_missing_runs": 0, "latency_censored_runs": 0, "llm_call_count": {"mean": 0.0, "measured_runs": 5, "missing_runs": 0}, "tool_call_count": {"mean": 44.6, "measured_runs": 5, "missing_runs": 0}, "input_tokens": {"mean": 0.0, "measured_runs": 5, "missing_runs": 0}, "output_tokens": {"mean": 0.0, "measured_runs": 5, "missing_runs": 0}, "llm_latency_ms": {"mean": 0.0, "measured_runs": 5, "missing_runs": 0}, "retrieval_latency_ms": {"mean": 0.0, "measured_runs": 5, "missing_runs": 0}, "tool_execution_latency_ms": {"mean": 7334.2304, "measured_runs": 5, "missing_runs": 0}, "total_tokens": {"mean": 0.0, "measured_runs": 5, "missing_runs": 0}}
Cost: {"accounted_usd": 0, "actual_usd": 0, "estimated_only_usd": 0, "unknown_reserve_usd": 0, "calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "usage_missing_calls": 0, "fallback_count": 0, "fallback_extra_accounted_usd": 0, "fallback_extra_actual_usd": 0}

Real API, PostgreSQL, receipt, queue and wire evidence. No LLM/Judge calls. Latency includes intentional timeout/lease waits and worker restarts. Worker interruption injects a committed intermediate task state; it does not kill a process inside a transaction.

Failed / Error cases:
- reliability-holdout-receipt-state-conflict: failed: Read-after-write Failure; Worker Recovery Failure; recovery_success_rate did not meet smoke expectation

Report: [reports/reliability/holdout/summary.md](../reports/reliability/holdout/summary.md)

Ground Truth erratum (not a new benchmark): the receipt-state-conflict inventory case was incorrectly labeled completed / one publication / awaiting_verification. The existing worker instead correctly blocked SOURCE_VERSION_CHANGED, produced zero effects and returned verification_failed. Original Holdout remains 4/5 (Task/Recovery 80%), including one label Evaluation Error; original automated failure categories and raw evidence are preserved. Only this canonical label and generator were corrected for future evaluation. No Agent/scorer change, rerun or rescore followed Holdout.
Frozen run dataset SHA256: c3da1cb24f39aba8f7f040f093e532ee1c770508c5da5fbc7a3a95084e186fdd; corrected future dataset SHA256: 4cb21c8d447c8e318ceb3b10ca90ce3c2910a99d4c0ee5185c5c86f4e813ca4f.
