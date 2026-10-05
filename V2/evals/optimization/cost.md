# Cost and context optimization

Latest status — 2026-10-05 continuation: stopped at an unmet Quality Gate after three unsuccessful repair rounds. Continuation business experiments were reverted; no new performance/cost reduction is certified. Historical entries below remain unchanged. Current accounting and retained architecture are recorded at the end.

Status: stopped with an unmet quality gate. Reasoning tokens are a subset of output tokens; never add them again to total usage. OpenRouter reported billing is authoritative. Embedding charges are unavailable and excluded.

Problem: Repeated document metadata and evidence anchors enlarged stage inputs.
Change: Retain every retrieved chunk body and UUID while removing the duplicate outer title/source/version wrapper already present in ingested headers. Remove a redundant quote from semantic-check claim lines after Python verifies it. Put stable document evidence before varying history/question.
Before: 8099.6 input tokens/request.
After: 6356.6 input tokens/request across the same five questions, a 21.52% combined reduction.
Why: Reduce duplicated context, not sources or history. Citation checks still see actual cited content. No new retrieval selection or chunk truncation was introduced.

Problem: Simple planning/generation/checks and unconditional review consumed unnecessary model output.
Change: Stage-specific primary limits, no reasoning for planning/simple generation/citation checks, and simple-question review gating. Complex generation/review and Support retain their original reasoning configuration and fallback policy.
Before: Output 2769.0, reasoning 1941.8, total 10868.6 tokens/request; 3.0 calls/request.
After: Output 1745.0, reasoning 687.8, total 8101.6 tokens/request; 2.6 calls/request. Combined total reduction 25.46%; reasoning reduction 64.58%.
Why: Reduce spend in simple stages while rejecting quality-damaging complex-review and Support experiments. No additional model was introduced.

Problem: Provider price changed between the historical baseline and this run.
Change: Report observed costs and separate any fixed-price estimate from actual billing.
Before: Actual $0.000377265/request.
After: Actual $0.000408396/request, an 8.25% INCREASE despite lower tokens. Primary prompt/completion prices were 50% higher. At baseline prices the observed final token/cache mix would imply approximately $0.000272264/request (27.83% lower); this is an estimate, not a bill.
Why: Token reduction does not guarantee a lower current bill when prices change.

Prompt caching: Existing provider automatic caching was observed; total cache reads 1024 -> 768 tokens across five requests. No improved hit rate or measured cache benefit is claimed. No cache pretends to complete a business action.

Task spend including development Agent/Judge calls and both five-request measurements:
{"evaluation_actual_usd": 0.0981880952, "evaluation_accounted_usd": 0.1025905352, "pending_or_unknown_reserve_usd": 0.00440244, "initial_profile_actual_usd": 0.0015672552, "final_profile_actual_usd": 0.0020419812, "known_added_openrouter_usd": 0.10179733160000001, "includes_both_agent_and_judge": true, "embedding_cost_known": false}

No new LLM calls are made when summarizing, inspecting Judge errors or writing these records. Experiments stay private; only baseline, stopped candidate and failed evidence are kept as formal performance reports.

## Current continuation / billing checkpoint

Problem: Answer completeness and source anchors were unstable after the architecture changes.
Change: Reuse prior traces/answers, test only affected cases and small regressions, review Judge failures offline, and revert the unsuccessful Generation edits. No new Support/context/provider experiment or five-request measurement was started.
Before: Entry architecture ledger actual $0.0269527608.
After: Ledger actual $0.0440248620; continuation added $0.0170721012 across 77 completed Agent/Judge calls. The last rejected seven-case Agent cohort averaged 5621.71 tokens and actual $0.0002796192/request; different case mix and rollback mean this is not a final cost improvement.
Why: Keep billed fees and rejected experiments separate from certified application savings. No Ground Truth changes or new Agent calls were used to repair a Judge score.

The saved five-request reference predates the final entry binding changes: input 4910.4, output 914.6, total 5825, reasoning 416.2 tokens/request; 1.4 LLM calls/request; actual $0.00028134288/request. Actual tool operations 1.6/request; native child spans 1.4/request because a GetWorkerTask read lacked a child span. Reasoning is already included in output tokens. There is no new matched Baseline -> Final measurement, so this continuation claims no percentage saving.

Retained earlier architecture: one simple / two normal complex RAG calls; no independent Completeness Review or semantic Citation LLM; original complex generation/Support reasoning retained; conditional planning, full retrieved content and compact Support evidence preserved. Existing automatic provider caching is observed, not a new implemented cache. Same-Decision evidence reuse remains; fresh Pre-write, receipt/unknown-state lookup, Verification, idempotency and leases are retained.

| Current architecture round accounting | Confirmed actual USD | Accounted USD |
|---|---:|---:|
| All targeted Agent/Judge ledger, including this continuation | 0.0440248620 | 0.0449300460 |
| Already completed five-request profile (not rerun) | 0.0014067144 | 0.0014067144 |
| Earlier saved-evidence Support diagnosis replay (not rerun) | 0.0002900520 | 0.0002900520 |
| Known architecture-round added total | 0.0457216284 | 0.0466268124 |

One unresolved earlier-call reserve $0.000905184 is not a confirmed fee. Ledger has 208 completed calls and one earlier pending/unknown call. Embedding charges remain unknown/excluded. Previous-round known cost $0.1017973316 remains a separate historical scope; do not confuse it with this continuation's $0.0170721012.

Saved primary model/provider: deepseek/deepseek-v4-flash / StreamLake. No provider comparison, reduced complex reasoning or Context Framework was adopted. Previously recorded price changes can offset token savings; actual response/ledger cost remains authoritative. Resume performance work only after affected failures + 3–5 original regressions pass stably. Current report and failure evidence are under `reports/performance/optimized/`.
