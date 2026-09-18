# Phase 9 / Task 14 — Dry Run 与 Benchmark 预算报告

状态：**Preparation Complete / Full Benchmark Awaiting Approval**  
日期：2026-09-18

## 1. 本轮完成内容

- 开发集已整理为 50 个案例：20 QA、12 Order、12 Shipment、3 Stock、3 Ticket。
- 新增 Eval Runner、独立结果检查和报告聚合，保留失败、错误、未评分案例和原始用量，不更换指标分母。
- 固定三类对照：Retrieval 48 次、Investigation Strategy 48 次、Agent Roles 80 次，共 176 次。
- 执行 3 个案例的 validate-only Dry Run：QA、Order、Ticket 各 1 个；0 次模型调用、0 次外部调用、3 个均明确标记 `not_scored`，没有冒充质量评测成功。
- 完整 Benchmark 和 Holdout 重复运行均未启动。`eval run --mode full` 在获得确认前由普通代码拒绝。

## 2. Dataset 与 Holdout 隔离

开发集验证结果：50 cases、ID 唯一、20 个固定对照案例全部存在、资料来源路径全部存在。

冻结 Holdout 只执行摘要与 SHA-256 校验，没有由 Eval Runner 加载案例内容：

- 状态：`frozen`
- 摘要案例数：30
- SHA-256：`87422e35a04b7bff9e4ce23d051c20c2386e46953c7f32d3c71dd108b3ed8a2a`
- 当前文件 Hash 与冻结摘要：一致
- 开发过程没有修改 `evals/holdout.jsonl` 或期待结果；默认加载会抛出权限错误。

## 3. 准备执行的正式测试

| 组别 | 固定案例 | Variants | Repeat | Runs | 主要指标 |
| --- | ---: | --- | ---: | ---: | --- |
| Retrieval | 8 QA | vector_only / hybrid / hybrid_rerank | 2 | 48 | source hit、citation、latency、tokens |
| Investigation | 6 Order + 6 Shipment | fixed_tools / dynamic_tools | 2 | 48 | success、tool calls、invalid calls、early stop、budget、latency |
| Agent Roles | 8 QA + 6 Order + 6 Shipment | single_role / dual_role | 2 | 80 | role boundary、success、safety、latency、tokens |
| Frozen Holdout | 30 hidden cases | full system | 3 | 90 | 完整质量、安全和失败清单 |
| Deferred acceptance | Phase 4/6/7/8 | real LLM + remote trace checks | 1 | 9 | Tool paths、budget stop、Trace queryability |

正式数据流仍计划为：案例 → 隔离业务场景 → 真实商家模型会话 → 现有批准路径 → 实际结果 → 独立检查 → 聚合报告。不会让评测程序直接写 `approved`、业务结果或成功状态。

## 4. 调用、Token、费用和时间估算

以下为保守规划值，不是已观测 Benchmark 结果。Google 输出量包含可能的 thinking tokens。价格日期为 2026-09-18：Groq `openai/gpt-oss-20b` 为 USD 0.075/1M input、0.30/1M output；Google `gemini-3.8-flash` 采用 2026-12-31 前的 USD 0.75/1M input、3.75/1M output。官方当前没有单列 `gemini-embedding-001` 的付费价格，因此只用 `gemini-embedding-2` 的 USD 0.20/1M text input 作为透明估算代理，实际 embedding 成本仍记为未知。

| 范围 | Runs | Groq calls | Google calls | Embedding calls | Groq tokens in/out | Google tokens in/out | Embedding tokens | 估算费用 USD | 理想串行时间 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 176 次对照 | 176 | 216 | 280 | 64 | 339,200 / 40,800 | 712,000 / 331,200 | 1,920 | 1.8141 | 48.67 分钟 |
| Holdout 30×3 | 90 | 180 | 180 | 45 | 315,000 / 40,500 | 450,000 / 180,000 | 3,150 | 1.0489 | 27.00 分钟 |
| 延期外部验收 | 9 | 9 | 18 | 约 3 | 7,200 / 900 | 40,500 / 18,000 | 90 | 0.0987 | 3.75 分钟 |
| 全部计划 | 275 | 405 | 478 | 约 112 | 661,400 / 82,200 | 1,202,500 / 529,200 | 5,160 | 2.9617 | 79.42 分钟 |

考虑限流、503、场景重置和服务启动，建议实际预留时间：

- 176 次对照：60–90 分钟。
- Holdout：35–60 分钟。
- 延期外部验收：10–20 分钟。
- 全部计划：约 105–170 分钟。

建议预算上限为 **USD 3.70**，即当前 USD 2.9617 估算增加 25% 余量。失败的 400/500 Google 请求按官方说明通常不收费，但仍消耗配额；因此余量主要覆盖真实 token 波动、thinking tokens 和 fallback，而不是把失败请求当作零运行成本。

## 5. 模型与 Trace

- Groq：`openai/gpt-oss-20b`，用于 QA、Query Plan、Citation Check 和 dual-role handoff。
- Google：`gemini-3.8-flash`，用于复杂调查；仅临时服务错误有限 fallback 到 `gemini-3.6-flash`。
- Embedding：`gemini-embedding-001`；Reranker 仍为本地 Qwen3，不产生远端模型调用费用。
- 本轮 Dry Run 没有调用任何模型或上传 LangSmith Trace。最小前端 E2E 使用一次真实 Groq，并显式关闭 LangSmith。远端 Trace 查询仍包含在 9 条延期验收计划中。

## 6. 测试和门禁

- Eval 定向测试：6 passed。
- 最小 Playwright E2E：1 passed。
- 完整 Benchmark：0 runs。
- Holdout 完整运行：0 runs。
- 未获得下一次确认前，CLI 的 full mode 保持锁定。

## 7. 已知限制

- Dry Run 只验证 Dataset、Runner、检查器、分母、输出和预算计算，不代表 Agent 质量。
- 当前时间是基于串行执行的规划估算；外部 high demand 可能显著拉长实际时间。
- Embedding 费用使用明确标记的代理价格，不能当作 `gemini-embedding-001` 的官方报价。
- 实际正式运行前仍需确认预算上限，并验证每个 comparison adapter 使用同一身份、场景和安全检查。

## 8. 停止点

本轮停在正式 Benchmark 之前。等待用户确认是否按上述范围、USD 3.70 预算上限和预计时间执行 176 次对照、90 次 Holdout 以及 9 条延期外部验收。
