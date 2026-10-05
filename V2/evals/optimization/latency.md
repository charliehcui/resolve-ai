# Latency optimization

Latest status — 2026-10-05 continuation: Quality Gate NOT PASSED. Three targeted repair rounds did not stabilize answer completeness and anchors. The continuation's Generation prompt/schema experiments were reverted to the entry working tree; prior Quick RAG/Support/Decision-reuse work remains. No final complete 30, new five-request measurement, Holdout or new trace was run. The initial entries below are historical; the checkpoint at the end describes current runtime.

Status: stopped; targeted quality gate failed. The measured configuration is not certified by a final complete Development run. All values below are from the same five representative real API questions, not a population latency SLO.

Problem: Simple facts and a single knowledge gap always invoked a separate Completeness Review.
Change: Skip review for short, single questions without history or compound/conditional wording. Keep original complex generation and LOW review; do not reduce the complex review budget.
Before: Four LLM calls per RAG request; simple 26.241s, knowledge gap 27.390s.
After: Three calls for those two requests; simple 10.646s, gap 12.223s. Complex requests still use four calls.
Why: Remove a redundant simple-case stage while retaining the difficult-case mechanism. These combined measurements do not isolate the contribution of each edit.

Problem: Citation semantic checks spent excessive time generating reasoning and repeated explanations.
Change: No reasoning and a 1536-token structured limit on the primary model; supported results use empty reasons. Keep the existing all-claim batch, full cited chunks, Python quote/UUID/company/version/date/scope checks and original fallback allowance.
Before: Citation validation across the three RAG representatives took approximately 36.411s.
After: 9.991s across the same three requests.
Why: Semantic support still needs a model; deterministic checks remain in Python. Batch validation and selecting cited chunks already existed before this task.

Problem: Query planning generated hidden reasoning and sometimes invalid product/version filters or truncated output.
Change: Primary planning uses no reasoning and a 1024-token limit; structured metadata permits one product identifier and one numeric product version, not explanatory text or multiple versions.
Before: Invalid version prose could yield zero retrieved chunks; a saved mapping case ended with two planning output errors.
After: Related sync, mapping, paid, worker retry, shipment and stock checks passed; no planning error in the final five real requests.
Why: Keep model intent/scope routing instead of bypassing it. Existing authorization and product/version filters remain active.

Combined Before: P50 26.241s; P95 112.607s.
Combined After: P50 12.223s; P95 84.481s. P95 <=20s was NOT met.

Remaining: Multi-source Completeness Review took 59.748s with 4598 output tokens, including 2296 reasoning tokens. Answer Generation took 33.937s. These stages remain sequential. Faster complex-review configurations were rejected after genuine omissions/truncation; no further iteration is performed.

Quality stop: Latest targeted plus four originally passing regressions scored 6/8, Core 33/35, Unsupported 0/29, Error/Timeout 0/0. Stock-version positive definition and re-reading current state after historical errors remain missing. The reviewer added no missing answers; citation validation removed no claims in either failure. Final complete 30 was blocked. No Ground Truth, sources, retrieval, Safety/Reliability logic or Holdout was changed/run.

## Current architecture / quality checkpoint

Problem: The saved engineer-recheck answer confuses a read result with the upper workflow outcome; generated quotes can also remove otherwise needed mapping/configuration conclusions.
Change: Tested general result/state coverage, supplied-premise guidance and Generation field ordering. Reverted these continuation experiments after regression; no new business optimization is retained. The earlier independent-review and semantic-citation removals remain in place.
Before: Earlier completed boundary subgroup 6/6, Core 22/22, Unsupported 0/16; separate original-failure subgroup 2/4. Entry quote-binding changes were still not fully validated.
After: Last rejected candidate 3/7, Core 21/28, Unsupported 0/18, Error/Timeout 0/0. Failed engineer-recheck, mapping-evidence, stock-version and OAuth config. These are rejected-candidate measurements, not a new retained-code score. Retained code has no new final full Development validation.
Why: Do not weaken literal quotation/scope checks or treat omitted facts as a pass. Three repair rounds did not produce stable quality, so further model iterations on this problem stop under the user's efficiency rule.

Runtime RAG: simple normally 1 LLM; complex normally 2 with conditional Query Understanding. Independent Completeness Review and Citation Semantic LLM Validation are deleted. Python retrieved UUID/company/product/version/date/continuous-quote checks remain. Recovery/fallback can increase physical calls above normal counts. Current quote-first schema and chunk-ending markers belong to the entry candidate and remain unvalidated as a complete release.

Runtime Support: known primary reads run in the existing parallel batch before normally 1 diagnosis call; additional investigation allows a second call. Current Decision reads can be reused by proposal construction; fresh approval/current-fact Pre-write and independent Verification remain. This continuation made no Support/tool/executor changes because quality did not unlock that phase.

Last existing five-root profile, predating the entry's last binding changes: P50 15.187s; P95 23.566s; simple RAG 8.081s, multi-source 18.400s, no-answer 9.181s, simple Support 15.187s, complex Support 24.857s. It is reference evidence, not final validation of the current code. Multi-source Generation LLM 14.538s, Retrieval 1.309s, deterministic Citation 0.033s and composition 0.000215s; no Review time. Complex Support had two serial LLM calls (7.560s + 16.167s) separated by parallel reads. That remains the next performance audit after quality passes.

The rejected seven-case cohort measured P50/P95 22.763s/48.427s, 2.0 LLM calls/request and 5621.71 tokens/request. Its different cases and rejected revision prohibit comparison as a final five-request improvement. No P95 <=20s claim is justified.

Protection regression: 61 deterministic tests passed, covering action authorization/fact changes, duplicate/unknown execution, response loss, idempotency, read-after-write, false-success detection and RAG/citation guards. The rollback restores the original models.py hash; business protection logic was unchanged. One Judge quote error was reviewed using the saved SLA answer offline; raw error retained and no Agent/Judge rerun for regrading. Ground Truth and dataset hash remain unchanged.

Next: use saved engineer/mapping/stock/OAuth failure evidence, address business-layer selection and exact anchors in the existing single Generation, then affected cases + 3–5 original passing regressions. Only stable quality unlocks Support audit, one fixed five-request measurement and the one remaining final complete 30. No Holdout. Full status and handoff: `reports/performance/optimized/summary.md`.
