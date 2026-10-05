# Reliability Optimization

## Unknown execution lease

Problem: An unknown request without a receipt could submit again before the active lease expired.
Change: Preserve the live lease for both claimed and unknown executions; keep receipt lookup before any retry.
Before: `recover-unknown` failed; a second write occurred during the live lease.
After: The case passed; no early write, then one safe retry after expiry using the same Request ID and execution.
Why: Unknown does not prove that the previous request stopped running.

## Verification deadline

Problem: Accepted orders were marked permanently failed when the worker had not finished within the verification wait.
Change: Keep awaiting verification while the receipt task is unfinished or unavailable; make a final failure decision only after a confirmed terminal task outcome.
Before: Pending-worker and receipt-conflict cases failed (0/2); a later completed order retained a failed action status.
After: Both passed (2/2); worker resume verified the completed order, while changed source facts produced truthful verification failure with no order effect.
Why: A waiting deadline is not proof of business failure, and an accepted receipt is not proof of success.

Validation: One targeted run, 3 affected failures + 3 related regressions, passed 6/6. Relevant action and scoring unit checks passed. No Ground Truth was changed after Baseline; no Holdout was executed during optimization.

Final complete Development validation: 13/13 passed; Task/Recovery/Idempotency 100%, Duplicate/False Success 0%, Error/Timeout 0. Agent, scoring and dataset frozen before first Holdout.
