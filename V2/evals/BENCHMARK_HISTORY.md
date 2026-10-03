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
