# RAG Evaluation Dataset — 40 例冻结

审查日期：2026-10-04（Australia/Sydney）。30 Development + 10 全新 Holdout；36 answerable=true、4 answerable=false。保留并重新核对原有 10 个 RAG Case ID，新增 30 个 ID。所有新标签均在正式 Baseline 和优化前依据当前正式知识来源建立。没有运行 Agent、LLM、Embedding、Retrieval 或 Benchmark。

来源：23 份正式文件（13 Markdown、3 PDF、4 DOCX、3 XLSX），全部已检查正文；当前 company-a / merchant-console / 2.0 的 22 份全部有正向来源覆盖。历史 1.0 文件只审查适用边界，不能作为当前问题正确检索目标；版本案例以当前资料纠正旧说法，不要求召回已过滤的旧版本 Chunk。离线解析得到 181 Chunk，其中当前范围 179；这是本地文件检查，不声称执行过在线检索。

## 字段与两层标准

沿用现有 EvalCase 外层 category=rag，不新建并行数据文件。case_id/question 在外层；split/category（内容类型）/domain/answerable/expected_topic/expected_sources/source_groups 在 expected；expected_facts/forbidden_claims/source_evidence 在 claim_ground_truth。retrieval_ground_truth 兼容 expected_sources，required_facts 兼容 expected_facts；两组别名必须一致。scenario=documents，知识范围在 initial_state，案例假设不是运行时 DB 事实。

Layer 1（Retrieval）：expected_sources 是全部合法来源的扁平路径；source_groups 外层为 AND、组内为 OR，例如 [[A,B],[C]] 表示需要 A/B 中任一个和 C。来源必须属于当前范围、主题匹配；source_evidence 保存实际正文中的证据锚点，用来源及这些主题证据定位相关 Chunk，不能把 XLSX 里任意其他状态行算命中。锚点是可复查定位依据，不是要求回答照抄或要求同一 Chunk 包含全部事实。

Recall@5 = 前 5 个最终检索结果命中的必需来源组数 / 必需组总数。MRR@5 = 前 5 中首个相关主题结果的 1/rank，没有命中为 0；多来源完整性由 Recall 表示，不能因 MRR=1 就判完整。先截取实际 top 5，再按组去重；不把同来源重复结果当多次命中，不因重复先去重后补足 5 个。每次查询与模式分别计分，不把多个检索调用拼接排名；不固定唯一工具或读取顺序。

4 个不可回答案例仍有非空 expected_sources：这些是证明资料不足或限制范围的边界来源，不是所请求细节的答案来源。检索边界证据可得 Retrieval 分数，编造细节仍使 Answer 失败；应单独汇总可回答与不可回答两组，避免混合掩盖拒答能力。明确写明不支持的功能存在确定答案（如 rag-scope），answerable=true；未提供准确时限、配置、入口或其他公司版本规则才为 false。

Layer 2（Answer）：按语义核对全部 expected_facts；允许准确转述、等价计算和条件式合法建议，不以关键词或唯一句式判分。forbidden_claims 列关键反例，同时不得增加任何没有知识依据的确定原因、当前状态、产品能力或自动处理承诺。否认错误说法、引用用户说法并纠正不算错误断言。answerable=false 时应指出具体资料缺口和适用边界，保留已知事实；按需要建议补资料/调查/Support，不要求真实创建工单或执行恢复。

## 覆盖统计

业务领域按单一主领域计数（组合案例可涉及其他领域，但不重复计总数）。

| 主领域 | Cases |
|---|---:|
| Order Sync | 3 |
| Order | 3 |
| SKU Mapping | 3 |
| Shipment | 3 |
| Authorization / Connection | 4 |
| Error / Evidence | 3 |
| Inventory | 3 |
| Unsupported Features | 2 |
| Worker | 3 |
| Waiting / Escalation | 2 |
| Recovery Actions | 3 |
| Approval / Role | 2 |
| Support Ticket | 3 |
| Version / Legacy | 3 |

| 内容类型 | Development | Holdout | 总数 |
|---|---:|---:|---:|
| Direct Fact | 7 | 0 | 7 |
| Rule / Condition | 10 | 1 | 11 |
| Multi-Source | 4 | 5 | 9 |
| Similar / Confusing Knowledge | 5 | 1 | 6 |
| No-Answer / Unsupported | 2 | 2 | 4 |
| Version / Scope | 2 | 1 | 3 |

单来源足够：29；需多个来源共同回答：11（其中 9 个主要类型为 Multi-Source，另 2 个为 Version / Scope）。组内替代来源不增加多来源计数。不可回答：4（Development 2 + Holdout 2）。

## Holdout

| Case ID | 新场景 |
|---|---|
| rag-holdout-auth-stock-approval | 授权过期、订单同步关闭与库存恢复的员工确认边界。 |
| rag-holdout-unknown-shipment-ticket | 未知发货核对可完成任务，但三端版本冲突仍不能关闭工单。 |
| rag-holdout-mapping-stock-partial | 订单关联 SKU 空值与库存来源时间无法解析同时出现。 |
| rag-holdout-duplicate-order-gap | 平台重复创建、内容冲突与 Worker 启动恢复的不同范围。 |
| rag-holdout-worker-dispatch | 订单已完成但派单失败，订单重试动作不能用于派单。 |
| rag-holdout-cross-shop-forbidden | 目标店铺读取被拒，不能借另一店铺同名订单推断或拼证据。 |
| rag-holdout-ticket-reopen-export | 已关闭工单复查需补资料，导出不扩大工程师只读授权。 |
| rag-holdout-lease-cancel | 资料未提供强制取消有效执行租约的公开接口或按钮。 |
| rag-holdout-legacy-payment-menu | 旧版支付菜单说法不能证明当前支持已有订单支付更新。 |
| rag-holdout-foreign-tenant-version | company-a 2.0 资料不能保证 company-b 3.0 的库存舍入规则。 |

Baseline / Optimization 只能选 category=rag 且 expected.split=development 的完整 30 例。10 个 Holdout 的模型运行标记为 never_executed，不能加入开发采样、失败分析或优化；最终验证才运行。读取文件、检查字段和本地解析不属于让 Agent 做题。本次没有在模型前提交过任一 Holdout 问题。

## 冻结与验证

Dataset SHA256：`26bc05579ac19439f51fc58868362c9532c8ff9d809eae392ee07926a5921144`。

算法：只取 RAG 条目，EvalCase.model_dump 规范化并按 case_id 排序；与 version=rag-40-current-v1 及 dataset.py 中 23 份文件的 RAG_SOURCE_HASHES 封装；UTF-8、ensure_ascii=False、sort_keys=True、separators=(",", ":") 序列化后求 SHA256。数据、分组、答案和实际来源字节都参与冻结，未把 Chunk 数或运行时 ID 冻结为答案。后续 Chunk 划分改变不应改变语义标准。

只读验证命令：`python -m evals.dataset --validate-rag`。检查加载、唯一 ID、30/10 分组、字段与别名、23 源快照、当前范围、22 源覆盖，以及 150 条证据在正式正文和离线 Chunk 中存在；不读写 DB，不调用模型、生成向量或执行检索。静态检查通过。没有修改 RAG、Agent、Prompt 或知识文件，73 条非 RAG 数据逐项保持一致，Workflow 冻结 Hash 保持不变。

Optimization Agent 不得改问题、标签、分组、证据或冻结常量来适应模型表现。若知识更新或发现真实标签疑点，应单独记录来源依据并人工确认后版本化，不能静默重写或替换 Holdout。

## Baseline 接入条件

Dataset 本身可冻结交付。现有旧 Evaluation 入口还不能直接代表本规范：evals/execute.py 把匹配来源的全部 Chunk 当分母；evals/judge.py 的 judge_rag 只枚举顶层 Markdown；evals/harness.py 的 Quick 默认只选五个 RAG，手选 RAG Holdout 尚无分组拦截，Final 会混入其他模块和 Holdout。正式 Baseline 执行者须先接入全部格式、source_groups/expected_topic、answerable/forbidden_claims 和严格 split 筛选，再在 30 个 Development 上运行。按本轮范围未修改这些评分/执行代码，也未验证在线模型质量。
