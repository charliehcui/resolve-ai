# Performance 优化记录

## 最终决定

2026-10-05 的续轮在三轮定向修复后停止：Quality Gate 未通过，答案完整性和原文锚点未稳定。续轮 Generation Prompt / Schema 实验已撤回，保留此前 Quick RAG、Support 预读取、上下文精简和同一 Decision 的证据复用。本记录合并原 latency.md 与 cost.md，历史数值和失败均保留。

没有重新运行最终完整 30 例、五请求测量、Holdout 或新 Trace。下面的 profiling 来自有限代表性请求，不能当作总体时延 SLO 或当前代码的最终质量认证。历史独立 Review / 语义 Citation 优化与后来的完全移除属于不同阶段。

## 阶段一：减少独立 Review 与引用检查开销

### 简单问题的 Completeness Review

- **问题（Problem）**：简单事实和单个知识缺口也总是调用独立 Completeness Review。
- **原因（Why）**：简单输入重复经过一个模型阶段；困难问题仍需要原完整推理和 Review 预算。
- **修改（Change）**：对无历史、无复合或条件表达的简短单问跳过 Review。保留复杂 Generation、LOW Review 和原 Fallback 规则，没有降低复杂 Review 预算。
- **之前（Before）**：RAG 每请求四次调用；简单问题 26.241s，知识缺口 27.390s。
- **之后（After）**：上述两请求各三次调用；分别为 10.646s、12.223s，复杂请求仍四次。联合测量不能单独归因到某一修改。

### Citation Validation

- **问题**：语义引用检查花费大量时间生成推理和重复解释。
- **修改**：主模型关闭推理，结构化输出限制为 1536 Token，通过结果使用空理由。保留原全断言批处理、完整被引用片段、Python 原文 / UUID / 公司 / 版本 / 日期 / 范围检查及原 Fallback 额度。
- **之前 → 之后**：相同三个 RAG 代表请求的引用检查合计约 36.411s → 9.991s。
- **原因**：这个历史阶段仍用模型判断语义支持；批量验证和选择被引用片段本来已存在，不能报告成新增机制。

### Query Planning

- **问题**：规划生成隐藏推理，产品 / 版本过滤有时含解释文字或多个版本，还可能输出截断。
- **修改**：主规划关闭推理，限制为 1024 Token，只允许一个产品标识和一个数值版本；仍保留模型意图 / 范围路由和原授权过滤。
- **之前**：无效版本文字可能使检索为空；保存的 mapping 案例出现两次规划输出错误。
- **之后**：sync、mapping、paid、worker retry、shipment、stock 相关检查通过；最终五个真实请求没有规划错误。

### 该阶段停止原因

| 同一五请求测量 | Before | After |
|---|---:|---:|
| P50 | 26.241s | 12.223s |
| P95 | 112.607s | 84.481s |

P95 <=20s 未达到。多来源 Completeness Review 耗时 59.748s，输出 4598 Token，其中 Reasoning 2296 Token；Answer Generation 耗时 33.937s，两阶段串行。更快的复杂 Review 配置出现真实遗漏或截断，因此被拒绝。

最后的定向案例加四个原通过回归案例为 6/8，Core 33/35，Unsupported 0/29，Error/Timeout 0/0。stock-version 的正面定义和历史错误后的当前状态重读仍缺失；Review 未补齐答案，Citation 未删除断言，最终完整 30 被阻止。没有修改或运行 Ground Truth、知识来源、检索、Safety / Reliability 逻辑或 Holdout。

## 阶段一：Context、Token 与 Provider 价格

### 重复上下文

- **问题**：重复文档元数据和证据锚点扩大输入。
- **修改**：保留每个检索片段全文和 UUID，只移除导入正文已含的重复外层标题 / 来源 / 版本，以及 Python 已核验的重复引用文字。稳定证据放在变化的历史 / 问题之前。
- **之前 → 之后**：相同五请求输入 8099.6 → 6356.6 Token/request，联合减少 21.52%。没有新增检索筛选或片段截断，也没有减少来源或历史。

### 输出与调用

按阶段设置主模型限制，简单规划、Generation、Citation 不生成推理，简单问题不进入独立 Review。复杂 Generation / Review 和 Support 保留原推理及 Fallback；质量下降的复杂 Review 与 Support 实验被拒绝，没有新增模型。

| 指标 | Before | After |
|---|---:|---:|
| Output Token/request | 2769.0 | 1745.0 |
| Reasoning Token/request | 1941.8 | 687.8 |
| Total Token/request | 10868.6 | 8101.6 |
| LLM Calls/request | 3.0 | 2.6 |

总 Token 减少 25.46%，Reasoning 减少 64.58%。Reasoning 已包含在 Output 中，不能重复计入 Total。

### 真实费用与估算

主 Provider 的 Prompt / Completion 价格在基线与测量之间上涨 50%。实际费用为 $0.000377265/request → $0.000408396/request，尽管 Token 减少，账单仍上涨 8.25%。按旧价格估算相同 Token / Cache 组合约为 $0.000272264/request（低 27.83%），这是估算，不是实际账单。

已有 Provider 自动缓存读数在五请求中为 1024 → 768 Token，没有证据证明命中率或缓存收益提高，也没有用缓存伪造业务完成。

该任务包含开发 Agent / Judge 和两次五请求测量的原始费用记录：

```json
{"evaluation_actual_usd": 0.0981880952, "evaluation_accounted_usd": 0.1025905352, "pending_or_unknown_reserve_usd": 0.00440244, "initial_profile_actual_usd": 0.0015672552, "final_profile_actual_usd": 0.0020419812, "known_added_openrouter_usd": 0.10179733160000001, "includes_both_agent_and_judge": true, "embedding_cost_known": false}
```

OpenRouter 实际响应和账本是费用依据，未知 Embedding 费用排除。写总结、复核 Judge 错误和整理本记录没有新 LLM 调用。实验保留在私人目录，正式报告保留基线、停止候选和失败证据。

## 阶段二：Quick RAG 与当前保留架构

### 保留的修改

简单 RAG 正常路径通常 1 次 LLM 调用；复杂问题按需查询理解，正常为 2 次。独立 Completeness Review 和语义 Citation LLM 已移除；检索 UUID、公司、产品、版本、日期和连续原文检查仍由 Python 执行，检索片段全文保留。恢复与 Fallback 可能增加实际次数。

Support 在原并行批次中预读已知主要工具，通常一次 Diagnosis；进一步调查可增加第二次。方案构建复用当前 Decision 的读取证据，但审批、最新事实 Pre-write、回执 / Unknown 查询、独立 Verification、幂等和租约仍保留。复杂 Generation / Support 的原推理配置未降低，观察到的 Provider 缓存也不是新实现的缓存框架。

入口候选的 quote-first Schema、原文绑定和片段结尾标记仍未获得完整版本验证。

### 五请求历史参考

以下测量早于入口最后的绑定修改，续轮没有重新测量：

| 指标 | 历史参考 |
|---|---:|
| P50 / P95 | 15.187s / 23.566s |
| 简单 RAG | 8.081s |
| 多来源 RAG | 18.400s |
| No-Answer | 9.181s |
| 简单 Support | 15.187s |
| 复杂 Support | 24.857s |
| Input / Output / Total Token/request | 4910.4 / 914.6 / 5825 |
| Reasoning Token/request | 416.2 |
| LLM Calls/request | 1.4 |
| Actual USD/request | 0.00028134288 |

多来源请求的 Generation LLM 为 14.538s，Retrieval 1.309s，确定性 Citation 0.033s，组合 0.000215s，没有 Review 耗时。复杂 Support 的两次串行 LLM 为 7.560s + 16.167s，中间有并行读取。

实际工具操作 1.6/request，原生子 Span 1.4/request，因为一次 GetWorkerTask 读取没有子 Span。没有新的匹配 Baseline → Final 测量，因此续轮不宣称百分比收益，也不能宣称 P95 <=20s。

## 续轮：质量失败、撤回与费用

- **问题**：engineer-recheck 把读取结果与上层工作流结果混淆；生成引用还可能删掉必要的 mapping / configuration 结论。
- **修改**：测试通用结果 / 状态覆盖、题设提示和 Generation 字段顺序。三轮未产生稳定结果，续轮实验已撤回；没有保留新的业务优化，也没有启动 Support、工具、执行器、Context、Provider 对比或新五请求实验。
- **之前**：边界子组 6/6，Core 22/22，Unsupported 0/16；另一个原失败子组 2/4，入口引用绑定修改尚未完整验证。
- **之后**：最后被拒绝候选 3/7，Core 21/28，Unsupported 0/18，Error/Timeout 0/0；engineer-recheck、mapping-evidence、stock-version、OAuth config 失败。这些是已撤回候选的成绩，不能赋给恢复后的代码。
- **原因与决定**：不能弱化原文 / 范围检查或把缺事实算通过；按用户效率要求停止。保留代码没有新的最终完整 Development 验证。

被拒绝的七例组 P50/P95 为 22.763s/48.427s，LLM Calls 2.0/request，Token 5621.71/request；样本不同且代码已撤回，不能与五请求参考直接比较为最终提升。

保护回归 61 个确定性测试通过，覆盖授权 / 事实变化、重复 / Unknown、响应丢失、幂等、回读、False Success 与 RAG / Citation 检查。回滚恢复原 models.py 哈希，业务保护代码未改变。一个 SLA Judge 引用错误用保存答案离线复核，原错误保留；没有为改分重跑 Agent / Judge，Ground Truth 和数据集哈希不变。

### 续轮费用

入口架构账本实际 $0.0269527608 → $0.0440248620；续轮增加 $0.0170721012，来自 77 次已完成 Agent / Judge 调用。最后被拒绝七例组平均 5621.71 Token、实际 $0.0002796192/request；因为样本变化和回滚，不能报告为最终费用改善。

| 当前架构轮费用范围 | Confirmed actual USD | Accounted USD |
|---|---:|---:|
| 全部定向 Agent/Judge 账本，含续轮 | 0.0440248620 | 0.0449300460 |
| 已完成五请求 profile，未重跑 | 0.0014067144 | 0.0014067144 |
| 保存证据的 Support Diagnosis replay，未重跑 | 0.0002900520 | 0.0002900520 |
| 已知架构轮新增合计 | 0.0457216284 | 0.0466268124 |

一个早期未决调用预留 $0.000905184 不是已确认费用；账本有 208 次完成调用和一个较早的 Pending / Unknown。Embedding 仍未知并排除。上一轮已知费用 $0.1017973316 与本续轮 $0.0170721012 是不同范围。

保存的主模型 / Provider 为 deepseek/deepseek-v4-flash / StreamLake。没有采用 Provider 对比、降低复杂推理或 Context Framework；价格变化可能抵消 Token 节省。

## 最终停止边界

历史建议是先稳定受影响案例和 3–5 个原通过回归，再讨论 Support 审计、固定五请求测量及剩余一次最终完整 30；不是本次清理的后续执行指令。当前不运行这些工作，也不打开 Holdout。完整本地停止报告与失败证据位于 `reports/performance/optimized/`。
