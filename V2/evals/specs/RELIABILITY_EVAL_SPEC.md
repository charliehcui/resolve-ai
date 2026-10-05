# Reliability Evaluation

Dataset: 8 existing Development cases + 5 missing Development boundaries = 13 Development / Regression; 5 fresh Holdout combinations. All cases remain in `evals/data/smoke.jsonl` with `category=reliability`. Other categories and fixtures are frozen.

Coverage: valid order/shipment/inventory actions, duplicate confirmation/submission, lost response, actual HTTP ReadTimeout, unknown execution and live lease, accepted work with a paused worker, receipt/current-source conflict, processing-task startup recovery, and completed-task reuse. Holdout combines shipment response loss after completion, shipment timeout with unfinished work, inventory duplicate after lost-response recovery, shipment interruption after an existing effect, and accepted inventory with changed current facts.

Ground Truth is defined before Baseline. It requires one execution, receipt, approval and repair task; one stable Request ID; exact declared business effects and final state; receipt/task state; real read-after-write evidence; no intermediate false success. Unknown with no receipt may retry only after its live lease expires. Accepted receipt alone never proves completion. Current-source conflict must remain truthful failure/waiting, rather than fabricate recovery.

Metrics: Task Success (whole contract), Recovery Success (whole declared recovery contract, including truthful conflict handling), Duplicate Business Effect Rate, False Success Rate and Idempotency Success. Every planned case stays in success denominators. Missing business evidence makes adverse rates unknown. Injected/handled transport timeouts are recorded in execution evidence and are distinct from evaluation run timeouts.

Pass requires every declared invariant. Stop at Task and Recovery >=95%, duplicate/false success 0%, critical idempotency 100%, no Provider/Evaluation Error and no systemic valid-action failure. No LLM or LLM Judge is used. Latency includes deliberate fault/lease waits and worker restarts.

Worker faults reuse the existing committed `processing` task fixture approach and restart the real worker. This tests startup recovery and existing-effect deduplication, not process-kill timing inside an atomic SQL transaction. Evaluation-only readiness confirms startup recovery ran; completed tasks must remain unchanged.

Run complete Development Baseline once, targeted failures plus a few regressions for material generic fixes, complete final Development once, then freeze and run Holdout once. Reports: `reports/reliability/{baseline,optimized,holdout}/`; targeted results overwrite `reports/latest/`. Passed rows are compact; failed/error cases retain full SQL, receipt, execution and HTTP evidence. Holdout must never be used for further changes.

## Ground Truth erratum after the single Holdout

`reliability-holdout-receipt-state-conflict` originally expected a completed inventory task, one publication and `awaiting_verification`. This label conflicted with the pre-existing source-version guard in `simulator/services/worker.py:process_next_stock_task`. SQL evidence shows `blocked / SOURCE_VERSION_CHANGED`, zero publications and `verification_failed`; this is correct conservative behavior, not a worker or read-after-write defect.

Only that canonical label and its dataset generator were corrected after the completed run: effect count 0, receipt/task state blocked, final status verification_failed. The original frozen report contains the original label and dataset hash. Its raw result remains **4/5, Task/Recovery 80%**, including this one label Evaluation Error. No Agent/scorer changes, Holdout replay, or retrospective score adjustment were made. The corrected dataset is for future versioned evaluation and is not the dataset used for these three recorded results.
