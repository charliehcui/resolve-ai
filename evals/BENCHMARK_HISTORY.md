# ResolveAI 评测历史（Benchmark History）

记录范围包括 Workflow、RAG、Safety、Reliability 和 Performance。以下原始指标、失败分类和已记录的历史复核均保留；本次清理没有重评分。自动成绩与离线复核成绩分别展示，Holdout 原始成绩不改写。

`reports/` 是被 Git 忽略的本地原始证据，不随克隆提供。下文保留证据路径供本机核对；正式历史由本文与 [优化记录](optimization/workflow.md) 等文档承担。

目录：

- [Workflow](#workflow-基线baseline)
- [RAG](#rag-第一阶段基线)
- [Safety](#safety-基线)
- [Reliability](#reliability-基线)
- [Performance](#performance-基线与优化测量--2026-10-05)

下面开头的口径说明来自当时的 Workflow 周期，其后分别保留其他阶段。

本轮从固定的 50 个 Workflow 开始：Development / Regression 40，Holdout 10。
仅记录 Workflow Baseline、Optimized Development Result、Holdout Result，以及以后单独授权的 Final Benchmark。
Ground Truth 和 Fixture 未修改；评分采用固定语义要求、实际工具调用和独立业务回读。
五项核心指标：Task Success、Diagnosis、Tool Selection、Tool Argument、Handoff。Error / Timeout 保留在分母中。
费用包含 Agent 与同模型族语义评审（Semantic Judge），不把缺失证据计为通过。

## Workflow 基线（Baseline）

- 运行：`20261003T140140Z-80e203e2`；通过案例：26/40；失败：4; Error: 10; Timeout: 0.
- 本地报告：`reports/workflow/baseline/summary.md`

| 指标 | 数值 |
|---|---:|
| task_success_rate | 65.00% |
| diagnosis_accuracy | 75.00% |
| tool_selection_accuracy | 80.00% |
| tool_argument_accuracy | 92.50% |
| handoff_accuracy | 85.00% |

- 无依据声明 / 不完整动作（Unsupported Claim / Incomplete Action）：0 / 0；缺失语义证据：10.
- 失败分类（单例可包含多项）：{"Provider Error": 10, "Unnecessary Tool": 4, "Wrong Tool": 4}
- 应用延迟 P50 / P95：19819.476 / 47265.613 ms.
- 已知 Tokens（应用 + Judge）：678958；应用平均用量：12875.264705882353；缺失应用用量：6 次运行。
- 费用（应用 + Judge）：$0.0206006584；含未知预留的计账：$0.0206006584；未知预留：$0；Fallback 调用：10.

评分复核：原 25/40 调整为 26/40，因为冻结契约允许读取不可用后重试一次。原始输出和判定保留，这部分变化属于评分修正。参数评分独立于工具选择，Ground Truth 未修改。

## Workflow 优化后的开发集结果

- 运行：`20261003T150800Z-b8a3d259`；通过案例：39/40；失败：1; Error: 0; Timeout: 0.
- 本地报告：`reports/workflow/optimized/summary.md`

| 指标 | 数值 |
|---|---:|
| task_success_rate | 97.50% |
| diagnosis_accuracy | 97.50% |
| tool_selection_accuracy | 97.50% |
| tool_argument_accuracy | 97.50% |
| handoff_accuracy | 97.50% |

- 无依据声明 / 不完整动作（Unsupported Claim / Incomplete Action）：0 / 0；缺失语义证据：0.
- 失败分类（单例可包含多项）：{"Missing Tool": 1, "Premature Stop": 1, "Wrong Diagnosis": 1, "Wrong Handoff": 1}
- 应用延迟 P50 / P95：25609.475 / 43755.18 ms.
- 已知 Tokens（应用 + Judge）：737006；应用平均用量：11251.72972972973；缺失应用用量：3 次运行。
- 费用（应用 + Judge）：$0.0240139824；含未知预留的计账：$0.0240139824；未知预留：$0；Fallback 调用：9.

达到当时的停止条件：39/40；五项指标均为 97.5%；观察到的 Unsupported Claim / Incomplete Action 为零；无 Provider Error 或 Timeout。剩余边界是条件式人工请求在未完成必要调查时直接转人工，Agent 优化在此停止。

评分复核复用原证据：失败的条件式转人工案例缺少必要后台读取，Diagnosis 和 Handoff 从 100% 修正为 97.5%，39/40 不变。原语义判定保留，Baseline 用同一规则复核后不变。没有重跑 Agent/模型，也没有改 Ground Truth。

| Baseline → Optimized | Before | After | 变化 |
|---|---:|---:|---:|
| task_success_rate | 65.00% | 97.50% | +32.50 pp |
| diagnosis_accuracy | 75.00% | 97.50% | +22.50 pp |
| tool_selection_accuracy | 80.00% | 97.50% | +17.50 pp |
| tool_argument_accuracy | 92.50% | 97.50% | +5.00 pp |
| handoff_accuracy | 85.00% | 97.50% | +12.50 pp |
| 通过案例 | 26/40 | 39/40 | +13 |
| p50_latency_ms | 19819.476 | 25609.475 | +5789.999 ms |
| p95_latency_ms | 47265.613 | 43755.18 | -3510.433 ms |
| total_tokens（应用 + Judge） | 678958 | 737006 | +58048.0000000000 |
| actual_usd（应用 + Judge） | 0.0206006584 | 0.0240139824 | +0.0034133240 |

成功率提高没有同时降低全部运行成本：P50、已知总 Tokens 和总费用增加，P95 降低。Baseline 包含 10 个供应商失败案例及缺失的语义证据。

## Workflow 留出集（Holdout）结果

- 运行：`20261003T153836Z-77af7b8a`；通过案例：8/10；失败：1; Error: 1; Timeout: 0.
- 本地报告：`reports/workflow/holdout/summary.md`

| 指标 | 数值 |
|---|---:|
| task_success_rate | 80.00% |
| diagnosis_accuracy | 80.00% |
| tool_selection_accuracy | 90.00% |
| tool_argument_accuracy | 100.00% |
| handoff_accuracy | 80.00% |

- 无依据声明 / 不完整动作（Unsupported Claim / Incomplete Action）：1 / 1；缺失语义证据：1.
- 失败分类（单例可包含多项）：{"Evaluation Error": 1, "Incomplete Action": 1, "Unsupported Claim": 1, "Wrong Diagnosis": 1, "Wrong Handoff": 1}
- 应用延迟 P50 / P95：32950.456 / 50130.283 ms.
- 已知 Tokens（应用 + Judge）：224333；应用平均用量：14776.0；缺失应用用量：5 次运行。
- 费用（应用 + Judge）：$0.008691023；含未知预留的计账：$0.008691023；未知预留：$0；Fallback 调用：7.

首次且唯一一次 Holdout：8/10，一个 Failed、一个 Error。原始结果、判定及完整失败证据均未修改，该阶段 Holdout 后没有优化 Agent 或评分。

- flow-holdout50-outage-read-refusal：模型返回的 next_step=tool_calls 超出合法枚举，HTTP 409 终止会话。原分类 Evaluation Error 指应用或模型输出失败，并非评分器崩溃。
- flow-holdout50-stock-active：库存发布处于 processing，用户要求等待且不要刷新或创建人工工单，Agent 却创建工单。原评分记录 Wrong Diagnosis / Wrong Handoff / Unsupported Claim / Incomplete Action，后两项对应同一个被违反的等待条件。未发生库存刷新。

已知用量不含被拒绝的请求及未上报用量。Baseline 有 10 例缺少语义证据，Holdout 有一个。未计到 Unsupported Claim，不能证明缺失证据对应的回答没有问题。

整个 Workflow 周期含准备和定向调试：确认 OpenRouter 费用 $0.0735687418，包含未解决调用预留的计账费用 $0.0778242468，未解决预留 $0.0042555050；已知 Tokens 2217989，请求 425 次，低于要求的约 $0.10 增量预算。
完整 Development：完成一次 Baseline、一次优化后验证，没有额外完整验证。Holdout 一次。Dataset 与 Fixture 哈希不变，既有 Baseline 通过案例无退化。优化停止于 97.5%，条件式人工请求边界继续保留。
## RAG 第一阶段基线

- 运行：`20261004T062922Z-6ef9fd1a`；检索模式：`vector_only`；通过：3/30; Error / Timeout: 1 / 0.
- 本地报告：`reports/rag/baseline/summary.md`

| 指标 | 数值 |
|---|---:|
| task_success_rate | 0.1 |
| answer_accuracy_after | None |
| recall_at_5 | 0.08333333333333333 |
| mrr | 0.07777777777777778 |
| unsupported_claim_rate_after | None |

- 核验原始字段：{"failure_categories": {"Metadata / Scope Problem": 20, "Missing Relevant Source": 27, "Query Understanding / Rewrite": 26, "Provider Error": 1, "Unsupported Claim": 3, "Low Ranking": 1, "Multi-Source Incomplete": 5, "Wrong Source": 1, "No-Answer Failure": 2}, "no_answer": {"correct": 0, "total": 2, "missing_evidence": 0}, "unsupported_final_claims": 3, "forbidden_claims": 1, "provider_error": 1, "evaluation_error": 0}
- 延迟 P50 / P95：7929.957 / 28013.6 ms.
- 应用 + Judge 已知 Tokens：170620; OpenRouter 实际 / 计账：$0.005633146 / $0.005633146；缺失调用用量：1；Fallback 调用：1.
- Gemini 查询向量费用不在 OpenRouter 账本内；文档向量复用既有结果。


- 无依据声明指标计数（Unsupported Claim）：3/34 条已知断言; 原始最终断言标记：3. 包含已恢复调用的上游失败：1.

- 原自动通过案例存在事实覆盖遗漏；source_review.json 同时保留来源审查和原始判定。

## RAG 优化后的开发集结果

- 运行：`20261004T082533Z-dc5878e4`；检索模式：`hybrid`；通过：16/30; Error / Timeout: 1 / 0.
- 本地报告：`reports/rag/optimized/summary.md`

| 指标 | 数值 |
|---|---:|
| task_success_rate | 0.5333333333333333 |
| answer_accuracy_after | None |
| recall_at_5 | 0.9333333333333333 |
| mrr | 0.9083333333333333 |
| unsupported_claim_rate_after | None |

- 核验原始字段：{"failure_categories": {"Correct Retrieval but Wrong Answer": 10, "Low Ranking": 3, "Missing Relevant Source": 3, "Query Understanding / Rewrite": 1, "Multi-Source Incomplete": 2, "Wrong Source": 2, "Application Error": 1, "No-Answer Failure": 1}, "no_answer": {"correct": 1, "total": 2, "missing_evidence": 0}, "unsupported_final_claims": 0, "forbidden_claims": 0, "provider_error": 0, "evaluation_error": 0}
- 延迟 P50 / P95：24557.777 / 39154.923 ms.
- 应用 + Judge 已知 Tokens：389226; OpenRouter 实际 / 计账：$0.023691094 / $0.023691094；缺失调用用量：51；Fallback 调用：51.
- Gemini 查询向量费用不在 OpenRouter 账本内；文档向量复用既有结果。

- Baseline → Optimized 原始指标：task_success_rate: 0.1 → 0.5333333333333333; answer_accuracy_after: None → None; recall_at_5: 0.08333333333333333 → 0.9333333333333333; mrr: 0.07777777777777778 → 0.9083333333333333

- 无依据声明指标计数（Unsupported Claim）：0/107 条已知断言; 原始最终断言标记：0. 包含已恢复调用的上游失败：51.

- 目标未达到。首次完整验证为 25/30，最终为 16/30；保留最终结果，没有挑选较高成绩。最终运行恢复 51 次上游共享池 429，混合使用多个模型；更严格的评分也影响对比。Agent 在 Holdout 前冻结。

## RAG 第一阶段留出集结果

- 运行：`20261004T085208Z-1036ae10`；检索模式：`hybrid`；通过：4/10; Error / Timeout: 0 / 0.
- 本地报告：`reports/rag/holdout/summary.md`

| 指标 | 数值 |
|---|---:|
| task_success_rate | 0.4 |
| answer_accuracy_after | 0.4 |
| recall_at_5 | 0.5166666666666667 |
| mrr | 0.6333333333333333 |
| unsupported_claim_rate_after | 0.14285714285714285 |

- 核验原始字段：{"failure_categories": {"Missing Relevant Source": 7, "Multi-Source Incomplete": 5, "Wrong Source": 5, "Unsupported Claim": 2, "Query Understanding / Rewrite": 2, "Low Ranking": 1, "No-Answer Failure": 1}, "no_answer": {"correct": 1, "total": 2, "missing_evidence": 0}, "unsupported_final_claims": 2, "forbidden_claims": 0, "provider_error": 0, "evaluation_error": 0}
- 延迟 P50 / P95：30919.264 / 43701.751 ms.
- 应用 + Judge 已知 Tokens：138747; OpenRouter 实际 / 计账：$0.0094702168 / $0.0094702168；缺失调用用量：16；Fallback 调用：16.
- Gemini 查询向量费用不在 OpenRouter 账本内；文档向量复用既有结果。


- 无依据声明指标计数（Unsupported Claim）：4/28 条已知断言; 原始最终断言标记：2. 包含已恢复调用的上游失败：16.

- Holdout 在冻结后仅执行一次，原始结果保留；该阶段之后没有修改 Agent/RAG。
## RAG 优化后的开发集结果

- 运行：`20261004T131851Z-51f8f0b8`；检索模式：`hybrid`；通过：19/30; Error / Timeout: 0 / 0.
- 本地报告：`reports/rag/answer-optimization/final-development/summary.md`

| 指标 | 数值 |
|---|---:|
| task_success_rate | 0.6333333333333333 |
| answer_accuracy_after | 0.6333333333333333 |
| recall_at_5 | 0.95 |
| mrr | 0.9083333333333333 |
| unsupported_claim_rate_after | 0.024 |

- 核验原始字段：{"failure_categories": {"Correct Retrieval but Wrong Answer": 10, "Low Ranking": 3, "Missing Relevant Source": 2, "Multi-Source Incomplete": 2, "Wrong Source": 1, "Unsupported Claim": 3, "Query Understanding / Rewrite": 1, "No-Answer Failure": 2}, "no_answer": {"correct": 0, "total": 2, "missing_evidence": 0}, "unsupported_final_claims": 1, "forbidden_claims": 0, "provider_error": 0, "evaluation_error": 0, "core_fact_coverage": {"numerator": 129, "denominator": 143, "missing_evidence_cases": 0, "confirmed_fraction": 0.9020979020979021, "value": 0.9020979020979021}, "all_fact_coverage": {"numerator": 143, "denominator": 197, "missing_evidence_cases": 0, "confirmed_fraction": 0.7258883248730964, "value": 0.7258883248730964}, "expected_fact_coverage": {"numerator": 46, "denominator": 82, "missing_evidence_cases": 0, "confirmed_fraction": 0.5609756097560976, "value": 0.5609756097560976}, "structured_output_errors": 10, "structured_output_recoveries": 10, "structured_output_by_stage": {"answer_completeness": {"errors": 0, "recoveries": 0}, "answer_coverage_judge": {"errors": 2, "recoveries": 2}, "answer_generation": {"errors": 0, "recoveries": 0}, "citation_validation": {"errors": 0, "recoveries": 0}, "grounding_judge": {"errors": 2, "recoveries": 2}, "query_planning": {"errors": 6, "recoveries": 6}}, "upstream_provider_errors": 12}
- 延迟 P50 / P95：55454.27 / 80317.282 ms.
- 应用 + Judge 已知 Tokens：716187; OpenRouter 实际 / 计账：$0.0292672238 / $0.0292672238；缺失调用用量：12；Fallback 调用：12.
- Gemini 查询向量费用不在 OpenRouter 账本内；文档向量复用既有结果。

- 回答优化使用冻结的问题范围评分审计，应与复评后的既有 Development 回答比较。更早的全有或全无评分采用不同口径，不能直接测量 Agent 自身增益。

### 最终开发集离线复核（没有新增执行）

- 上面的 `20261004T131851Z-51f8f0b8` 自动结果保持 19/30。最终既有回答语义复核修正七个纯评分失败，得到 26/30（86.67%）；四个真实失败保留。
- 同一最终问题范围标准应用到旧 Development 回答，得到 21/30（70.00%）。原 16/30 → 21/30 属于评分修正；21/30 → 26/30 是观察到的流水线净变化，同时记录了 Fallback 差异及两个真实退化。
- Core Coverage 130/137（94.89%）；总体子句覆盖 143/197（72.59%）；严格完整的原 expected_facts 45/82。原事实全部保留。独立报告 Rubric 对旧、新回答统一增加六个 Optional 分类，正式 Rubric 和代码未修改。
- No-Answer 1/2；确认评分目录遗漏的原始工作簿行后，Unsupported Claims 为 2/125。Correct Retrieval but Wrong Answer 为 3。Execution/Provider/Evaluation Error 为 0/0/0；恢复 12 次上游失败及 10 次结构化输出失败。
- 复核没有新增 Agent、Judge、检索或 HTTP 调用；没有新增完整或定向运行，也没有再次执行旧 Holdout。本地最终报告和审计：`reports/rag/answer-optimization/final-offline-review/final.md`。停止时未宣称达到 95% 或无退化。

## Safety 基线


运行：20261004T153531Z-83350882
通过案例：19/20; Error 0; Timeout 0

| 指标 | 数值 |
|---|---:|
| task_success_rate | 0.95 (19.0/20) |
| unauthorized_action_blocking_rate | 1.0 (4.0/4) |
| invalid_action_rejection_rate | 0.9230769230769231 (12.0/13) |
| valid_action_completion_rate | 1.0 (3.0/3) |

安全原始字段（Safety）：{"unsafe_business_side_effects": 1, "missing_business_evidence_runs": 0, "failure_categories": {"Business Facts Not Rechecked": 1}}
错误原始字段（Errors）：{"total_executions": 20, "completed_executions": 20, "business_failed_executions": 1, "error_executions": 0, "timeout_executions": 0, "value": 1.0, "by_failure_kind": {}, "provider_call_errors": 0}
性能原始字段（Performance）：{"p50_latency_ms": 1976.271, "p95_latency_ms": 4385.871, "latency_measured_runs": 20, "latency_missing_runs": 0, "latency_censored_runs": 0, "llm_call_count": {"mean": 0.0, "measured_runs": 20, "missing_runs": 0}, "tool_call_count": {"mean": 7.1, "measured_runs": 20, "missing_runs": 0}, "input_tokens": {"mean": 0.0, "measured_runs": 20, "missing_runs": 0}, "output_tokens": {"mean": 0.0, "measured_runs": 20, "missing_runs": 0}, "llm_latency_ms": {"mean": 0.0, "measured_runs": 20, "missing_runs": 0}, "retrieval_latency_ms": {"mean": 0.0, "measured_runs": 20, "missing_runs": 0}, "tool_execution_latency_ms": {"mean": 1390.65895, "measured_runs": 20, "missing_runs": 0}, "total_tokens": {"mean": 0.0, "measured_runs": 20, "missing_runs": 0}}
费用原始字段（Cost）：{"accounted_usd": 0, "actual_usd": 0, "estimated_only_usd": 0, "unknown_reserve_usd": 0, "calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "usage_missing_calls": 0, "fallback_count": 0, "fallback_extra_accounted_usd": 0, "fallback_extra_actual_usd": 0}

使用确定性的 API 和 SQL 证据，没有 LLM Judge。直接准备已持久化的审批、不派发，用于测试审批后的事实变化；Fixture 变更不计入动作副作用。

失败 / 错误案例（Failed / Error）：
- safe-authorization-changed: failed: Business Facts Not Rechecked: expected status and SQL business/approval/scope invariants were not satisfied; Unsafe Business Side Effects: execution, receipt or unapproved business modification observed; invalid_action_rejection_rate did not meet smoke expectation

本地报告：`reports/safety/baseline/summary.md`

## Safety 优化后的开发集结果


运行：20261004T154525Z-a8995fdb
通过案例：20/20; Error 0; Timeout 0

| 指标 | 数值 |
|---|---:|
| task_success_rate | 1.0 (20.0/20) |
| unauthorized_action_blocking_rate | 1.0 (4.0/4) |
| invalid_action_rejection_rate | 1.0 (13.0/13) |
| valid_action_completion_rate | 1.0 (3.0/3) |

安全原始字段（Safety）：{"unsafe_business_side_effects": 0, "missing_business_evidence_runs": 0, "failure_categories": {}}
错误原始字段（Errors）：{"total_executions": 20, "completed_executions": 20, "business_failed_executions": 0, "error_executions": 0, "timeout_executions": 0, "value": 1.0, "by_failure_kind": {}, "provider_call_errors": 0}
性能原始字段（Performance）：{"p50_latency_ms": 1931.441, "p95_latency_ms": 3844.714, "latency_measured_runs": 20, "latency_missing_runs": 0, "latency_censored_runs": 0, "llm_call_count": {"mean": 0.0, "measured_runs": 20, "missing_runs": 0}, "tool_call_count": {"mean": 6.5, "measured_runs": 20, "missing_runs": 0}, "input_tokens": {"mean": 0.0, "measured_runs": 20, "missing_runs": 0}, "output_tokens": {"mean": 0.0, "measured_runs": 20, "missing_runs": 0}, "llm_latency_ms": {"mean": 0.0, "measured_runs": 20, "missing_runs": 0}, "retrieval_latency_ms": {"mean": 0.0, "measured_runs": 20, "missing_runs": 0}, "tool_execution_latency_ms": {"mean": 1311.23755, "measured_runs": 20, "missing_runs": 0}, "total_tokens": {"mean": 0.0, "measured_runs": 20, "missing_runs": 0}}
费用原始字段（Cost）：{"accounted_usd": 0, "actual_usd": 0, "estimated_only_usd": 0, "unknown_reserve_usd": 0, "calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "usage_missing_calls": 0, "fallback_count": 0, "fallback_extra_accounted_usd": 0, "fallback_extra_actual_usd": 0}

使用确定性的 API 和 SQL 证据，没有 LLM Judge。直接准备已持久化的审批、不派发，用于测试审批后的事实变化；Fixture 变更不计入动作副作用。

失败 / 错误案例（Failed / Error）：

本地报告：`reports/safety/optimized/summary.md`

## Safety 留出集结果


运行：20261004T155049Z-f119bda7
通过案例：5/5; Error 0; Timeout 0

| 指标 | 数值 |
|---|---:|
| task_success_rate | 1.0 (5.0/5) |
| unauthorized_action_blocking_rate | 1.0 (1.0/1) |
| invalid_action_rejection_rate | 1.0 (3.0/3) |
| valid_action_completion_rate | 1.0 (1.0/1) |

安全原始字段（Safety）：{"unsafe_business_side_effects": 0, "missing_business_evidence_runs": 0, "failure_categories": {}}
错误原始字段（Errors）：{"total_executions": 5, "completed_executions": 5, "business_failed_executions": 0, "error_executions": 0, "timeout_executions": 0, "value": 1.0, "by_failure_kind": {}, "provider_call_errors": 0}
性能原始字段（Performance）：{"p50_latency_ms": 2803.515, "p95_latency_ms": 4306.956, "latency_measured_runs": 5, "latency_missing_runs": 0, "latency_censored_runs": 0, "llm_call_count": {"mean": 0.0, "measured_runs": 5, "missing_runs": 0}, "tool_call_count": {"mean": 9.2, "measured_runs": 5, "missing_runs": 0}, "input_tokens": {"mean": 0.0, "measured_runs": 5, "missing_runs": 0}, "output_tokens": {"mean": 0.0, "measured_runs": 5, "missing_runs": 0}, "llm_latency_ms": {"mean": 0.0, "measured_runs": 5, "missing_runs": 0}, "retrieval_latency_ms": {"mean": 0.0, "measured_runs": 5, "missing_runs": 0}, "tool_execution_latency_ms": {"mean": 1890.9544, "measured_runs": 5, "missing_runs": 0}, "total_tokens": {"mean": 0.0, "measured_runs": 5, "missing_runs": 0}}
费用原始字段（Cost）：{"accounted_usd": 0, "actual_usd": 0, "estimated_only_usd": 0, "unknown_reserve_usd": 0, "calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "usage_missing_calls": 0, "fallback_count": 0, "fallback_extra_accounted_usd": 0, "fallback_extra_actual_usd": 0}

使用确定性的 API 和 SQL 证据，没有 LLM Judge。直接准备已持久化的审批、不派发，用于测试审批后的事实变化；Fixture 变更不计入动作副作用。

失败 / 错误案例（Failed / Error）：

本地报告：`reports/safety/holdout/summary.md`

## Reliability 基线


运行：20261005T004129Z-f4a39b99
通过案例：10/13; Error 0; Timeout 0

| 指标 | 数值 |
|---|---:|
| task_success_rate | 0.7692307692307693 (10.0/13) |
| recovery_success_rate | 0.7692307692307693 (10.0/13) |
| duplicate_business_effect_rate | 0.0 (0.0/13) |
| false_success_rate | 0.0 (0.0/13) |
| idempotency_success_rate | 1.0 (13.0/13) |

核验原始字段：{"failure_categories": {"Unknown State Mishandled": 1, "Valid Execution Incorrectly Failed": 2}, "missing_business_evidence_runs": 0, "metric_definition": "Recovery Success is satisfaction of the declared recovery contract, including truthful verification failure/waiting for conflicting current facts."}
错误原始字段（Errors）：{"total_executions": 13, "completed_executions": 13, "business_failed_executions": 3, "error_executions": 0, "timeout_executions": 0, "value": 1.0, "by_failure_kind": {}, "provider_call_errors": 0}
性能原始字段（Performance）：{"p50_latency_ms": 5191.403, "p95_latency_ms": 20200.996, "latency_measured_runs": 13, "latency_missing_runs": 0, "latency_censored_runs": 0, "llm_call_count": {"mean": 0.0, "measured_runs": 13, "missing_runs": 0}, "tool_call_count": {"mean": 16.76923076923077, "measured_runs": 13, "missing_runs": 0}, "input_tokens": {"mean": 0.0, "measured_runs": 13, "missing_runs": 0}, "output_tokens": {"mean": 0.0, "measured_runs": 13, "missing_runs": 0}, "llm_latency_ms": {"mean": 0.0, "measured_runs": 13, "missing_runs": 0}, "retrieval_latency_ms": {"mean": 0.0, "measured_runs": 13, "missing_runs": 0}, "tool_execution_latency_ms": {"mean": 3355.5256923076927, "measured_runs": 13, "missing_runs": 0}, "total_tokens": {"mean": 0.0, "measured_runs": 13, "missing_runs": 0}}
费用原始字段（Cost）：{"accounted_usd": 0, "actual_usd": 0, "estimated_only_usd": 0, "unknown_reserve_usd": 0, "calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "usage_missing_calls": 0, "fallback_count": 0, "fallback_extra_accounted_usd": 0, "fallback_extra_actual_usd": 0}

使用真实 API、PostgreSQL、回执、队列及网络证据，无 LLM/Judge 调用。延迟包含主动注入的超时、租约等待和 Worker 重启。Worker 中断通过注入已提交的中间任务状态模拟，没有在事务内杀进程。

失败 / 错误案例（Failed / Error）：
- recover-unknown: failed: Unknown State Mishandled; recovery_success_rate did not meet smoke expectation
- recover-pending-worker: failed: Valid Execution Incorrectly Failed; Valid Execution Incorrectly Failed: accepted unfinished work became terminal; recovery_success_rate did not meet smoke expectation
- recover-receipt-conflict: failed: Valid Execution Incorrectly Failed: accepted unfinished work became terminal; recovery_success_rate did not meet smoke expectation

本地报告：`reports/reliability/baseline/summary.md`

## Reliability 优化后的开发集结果


运行：20261005T005001Z-add057b8
通过案例：13/13; Error 0; Timeout 0

| 指标 | 数值 |
|---|---:|
| task_success_rate | 1.0 (13.0/13) |
| recovery_success_rate | 1.0 (13.0/13) |
| duplicate_business_effect_rate | 0.0 (0.0/13) |
| false_success_rate | 0.0 (0.0/13) |
| idempotency_success_rate | 1.0 (13.0/13) |

核验原始字段：{"failure_categories": {}, "missing_business_evidence_runs": 0, "metric_definition": "Recovery Success is satisfaction of the declared recovery contract, including truthful verification failure/waiting for conflicting current facts."}
错误原始字段（Errors）：{"total_executions": 13, "completed_executions": 13, "business_failed_executions": 0, "error_executions": 0, "timeout_executions": 0, "value": 1.0, "by_failure_kind": {}, "provider_call_errors": 0}
性能原始字段（Performance）：{"p50_latency_ms": 5211.467, "p95_latency_ms": 25708.642, "latency_measured_runs": 13, "latency_missing_runs": 0, "latency_censored_runs": 0, "llm_call_count": {"mean": 0.0, "measured_runs": 13, "missing_runs": 0}, "tool_call_count": {"mean": 19.0, "measured_runs": 13, "missing_runs": 0}, "input_tokens": {"mean": 0.0, "measured_runs": 13, "missing_runs": 0}, "output_tokens": {"mean": 0.0, "measured_runs": 13, "missing_runs": 0}, "llm_latency_ms": {"mean": 0.0, "measured_runs": 13, "missing_runs": 0}, "retrieval_latency_ms": {"mean": 0.0, "measured_runs": 13, "missing_runs": 0}, "tool_execution_latency_ms": {"mean": 3758.7354615384615, "measured_runs": 13, "missing_runs": 0}, "total_tokens": {"mean": 0.0, "measured_runs": 13, "missing_runs": 0}}
费用原始字段（Cost）：{"accounted_usd": 0, "actual_usd": 0, "estimated_only_usd": 0, "unknown_reserve_usd": 0, "calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "usage_missing_calls": 0, "fallback_count": 0, "fallback_extra_accounted_usd": 0, "fallback_extra_actual_usd": 0}

使用真实 API、PostgreSQL、回执、队列及网络证据，无 LLM/Judge 调用。延迟包含主动注入的超时、租约等待和 Worker 重启。Worker 中断通过注入已提交的中间任务状态模拟，没有在事务内杀进程。

失败 / 错误案例（Failed / Error）：

本地报告：`reports/reliability/optimized/summary.md`

Baseline → Optimized：10/13 → 13/13；Task/Recovery 76.92% → 100%（+23.08 个百分点）；Duplicate/False Success 0% → 0%；Idempotency 100% → 100%；Error/Timeout 0 → 0。P50/P95 5.191s/20.201s → 5.211s/25.709s，包含刻意注入的恢复等待。Tokens 0 → 0；费用 $0 → $0。

## Reliability 留出集结果


运行：20261005T005415Z-4065ee86
通过案例：4/5; Error 0; Timeout 0

| 指标 | 数值 |
|---|---:|
| task_success_rate | 0.8 (4.0/5) |
| recovery_success_rate | 0.8 (4.0/5) |
| duplicate_business_effect_rate | 0.0 (0.0/5) |
| false_success_rate | 0.0 (0.0/5) |
| idempotency_success_rate | 1.0 (5.0/5) |

核验原始字段：{"failure_categories": {"Read-after-write Failure": 1, "Worker Recovery Failure": 1}, "missing_business_evidence_runs": 0, "metric_definition": "Recovery Success is satisfaction of the declared recovery contract, including truthful verification failure/waiting for conflicting current facts."}
错误原始字段（Errors）：{"total_executions": 5, "completed_executions": 5, "business_failed_executions": 1, "error_executions": 0, "timeout_executions": 0, "value": 1.0, "by_failure_kind": {}, "provider_call_errors": 0}
性能原始字段（Performance）：{"p50_latency_ms": 11597.149, "p95_latency_ms": 26683.097, "latency_measured_runs": 5, "latency_missing_runs": 0, "latency_censored_runs": 0, "llm_call_count": {"mean": 0.0, "measured_runs": 5, "missing_runs": 0}, "tool_call_count": {"mean": 44.6, "measured_runs": 5, "missing_runs": 0}, "input_tokens": {"mean": 0.0, "measured_runs": 5, "missing_runs": 0}, "output_tokens": {"mean": 0.0, "measured_runs": 5, "missing_runs": 0}, "llm_latency_ms": {"mean": 0.0, "measured_runs": 5, "missing_runs": 0}, "retrieval_latency_ms": {"mean": 0.0, "measured_runs": 5, "missing_runs": 0}, "tool_execution_latency_ms": {"mean": 7334.2304, "measured_runs": 5, "missing_runs": 0}, "total_tokens": {"mean": 0.0, "measured_runs": 5, "missing_runs": 0}}
费用原始字段（Cost）：{"accounted_usd": 0, "actual_usd": 0, "estimated_only_usd": 0, "unknown_reserve_usd": 0, "calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "usage_missing_calls": 0, "fallback_count": 0, "fallback_extra_accounted_usd": 0, "fallback_extra_actual_usd": 0}

使用真实 API、PostgreSQL、回执、队列及网络证据，无 LLM/Judge 调用。延迟包含主动注入的超时、租约等待和 Worker 重启。Worker 中断通过注入已提交的中间任务状态模拟，没有在事务内杀进程。

失败 / 错误案例（Failed / Error）：
- reliability-holdout-receipt-state-conflict: failed: Read-after-write Failure; Worker Recovery Failure; recovery_success_rate did not meet smoke expectation

本地报告：`reports/reliability/holdout/summary.md`

Ground Truth 勘误（不构成新 Benchmark）：receipt-state-conflict 库存案例曾错误标为 completed / 一次发布 / awaiting_verification。实际既有 Worker 正确阻止 SOURCE_VERSION_CHANGED，产生零副作用并返回 verification_failed。原 Holdout 保持 4/5（Task/Recovery 80%），包含一个标签 Evaluation Error；原自动失败分类和证据保留。仅修正未来评测使用的规范标签及生成器。Holdout 后没有修改 Agent/评分器、重跑或重评分。
运行时冻结 Dataset SHA256：c3da1cb24f39aba8f7f040f093e532ee1c770508c5da5fbc7a3a95084e186fdd；修正后供未来使用的 Dataset SHA256：4cb21c8d447c8e318ceb3b10ca90ce3c2910a99d4c0ee5185c5c86f4e813ca4f。

## Performance 基线与优化测量 — 2026-10-05

状态：质量门槛未达到，已停止。优化列是测得的保留候选方案，未经发布质量认证。Baseline 复用历史 LangSmith 证据，最后五个代表性应用请求是新测量。没有完成最终完整的 30 Development 或 Holdout。

| 指标 | Performance Baseline | Performance Optimized（质量未验证） |
|---|---:|---:|
| P50 | 26.241s | 12.223s |
| P95 | 112.607s | 84.481s |
| 每请求 Tokens | 10868.6 | 8101.6 |
| 每请求实际费用 | $0.000377265 | $0.000408396 |
| 每请求 LLM 调用 | 3.0 | 2.6 |

最近定向加回归为 6/8；Core 33/35；Unsupported 0/29；Error/Timeout 0/0。两个真实遗漏继续判失败。更早被中断的尝试完成 12 例，没有跑完 30。全通过门槛始终未满足，P95 <=20s 未达到。新增已知费用与剩余未知预留保留在报告中；供应商价格上涨 50%，因此实际每请求费用增加 8.25%。

本地报告：`reports/performance/baseline/summary.md`, `reports/performance/optimized/summary.md`, `reports/performance/optimized/quality/targeted/summary.md`.

## Performance / Cost / Context 续轮 — 2026-10-05，质量门槛未通过

这是重要状态节点，不是 Final Benchmark。三轮定向修复没有让完整性和定位片段稳定。续轮生成实验已撤回，之前的 Quick RAG、Support 预取和上下文缩减、同一 Decision 复用仍保留。没有改变 Ground Truth、检索、安全可靠性执行器或评分规则。

- 最后撤回的候选方案：`20261005T083302Z-30c501e3`，3/7；Core 21/28；Unsupported 0/18；Error/Timeout 0/0。engineer-recheck、mapping-evidence、stock-version、OAuth-config 失败。原结果和完整失败证据保留，这些成绩不代表恢复后的代码。
- 更早的一次 SLA Judge 引文校验错误通过保存的回答离线审查，原错误保留。另一个 engineer 自动通过因遗漏工单 open 状态，被离线复核否决。复评分没有重跑 Agent/Judge，也没有修改 Ground Truth。
- 保护性回归：61 个确定性测试通过，没有运行完整 Safety/Reliability Benchmark。
- 质量门槛未通过。续轮没有最终完整的 30 Development、新的五请求测量、新应用根轨迹、Workflow 批次、供应商对比或 Holdout。当时预留的一次最终完整 30 仍未使用，等待稳定的质量与性能方案；本次清理不继续执行。上面的完整 Development/Holdout 仍属于历史记录。
- 既有五个根轨迹参考：P50/P95 15.187s/23.566s；简单 RAG 8.081s，多来源 18.400s，资料不足 9.181s，简单 Support 15.187s，复杂 Support 24.857s；LLM 1.4/request；总 Tokens 5825；推理 Tokens 416.2；实际 USD/request 0.00028134288。该测量早于最后的绑定修改，不是最终结果，也未认证 P95 <=20s。
- 续轮确认新增 OpenRouter 费用 $0.0170721012。当前架构轮已知总费用 $0.0457216284；计账 $0.0466268124，包含更早一次未知预留 $0.000905184。Embeddings 未包含，之前的性能轮费用独立记录。

本地续轮交接报告：`reports/performance/optimized/summary.md`。最近撤回候选的摘要、失败和离线复核：`reports/performance/optimized/quality/continuation/`。原完整索引、失败候选代码和日志保留在 `.local/quick-pipeline/`。Dataset SHA256 未变化：`4cb21c8d447c8e318ceb3b10ca90ce3c2910a99d4c0ee5185c5c86f4e813ca4f`。
