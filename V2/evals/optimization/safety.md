# Safety Optimization

Problem: Shop authorization could expire after approval while the shop version stayed unchanged. Order recovery still created a repair receipt and task before the Worker blocked it.

Change: Order and shipment recovery now re-read current shop authorization before creating an execution or submitting a repair. Failed reads and unauthorized states block the old plan.

Before: Baseline 19/20 (95%); Invalid Action Rejection 12/13 (92.31%); 1 unsafe submission. The six selected Development cases had 5/6 passes in Baseline.

After: Targeted failure + five regressions passed 6/6. Final Development passed 20/20 (100%); Invalid Action Rejection 13/13 (100%); unsafe effects 0. The authorization case created no execution, receipt or business change.

Why: Valid old approval cannot replace current authorization. Legal order, shipment and inventory actions must still succeed.

Evaluation preparation: Added five uncovered Development scenarios and five fresh Holdout combinations. Safety Task Success now includes Safety cases, and SQL side-effect checks include every business table, receipts, queues and other tenants. These corrections were completed before Baseline and are not counted as Agent improvement. No original Ground Truth conflicts were found; Workflow/RAG rows and frozen fixtures remain unchanged.
