# RAG 优化记录

固定 30 Development / 10 Holdout；问题、标签和 23 份来源保持冻结。只记录最终保留的重要优化。

## 1. 查询范围与意图

问题（Problem）： 公司名被当作产品条件；文档中的流程描述被误当作实际后台请求。
改动（Change）： 明确区分租户和产品，用登录公司的标识校正错误产品字段；保留完整查询条件并优先检索规则问题。
改动前（Before）： 首组选定案例 3/8 通过（其中 1 个 Embedding Provider Error）。
改动后（After）： 相同 8 个 Agent 答案复用复评后 6/8 通过，3 个已通过的回归案例全部保持通过。
原因（Why）： 修复访问范围和意图判断，不依赖 Case ID 或答案。
参考（Reference）： [WeKnora Query Understanding](https://github.com/Tencent/WeKnora/blob/bccb4b151bae403508da77fbb174efc79dc47c1a/config/prompt_templates/rewrite.yaml)：保留原问题的实体、条件和请求类型；适配为现有查询规划器的指引与登录范围校验。

## 2. 混合检索与上下文选择

问题（Problem）： 原问题在正确范围内仍有关键词排序和多来源覆盖不足；相同文件的多个片段占满前五名。
改动（Change）： 保留现有 BM25、Vector 和 RRF；采用 Hybrid，在候选池中优先选每个来源最多两个片段，保留第一名，来源不足时补满。
改动前（Before）： 相同 30 个缓存查询，Vector Recall@5 91.67%、MRR@5 82.44%；原 Hybrid 91.67%、94.17%。
改动后（After）： Hybrid 加来源覆盖后 Recall@5 96.67%、MRR@5 94.17%，没有检索退化；重放检索未再次调用模型。
原因（Why）： 小改动解决来源集中，避免 CPU Reranker 的额外延迟；原 Reranker Recall 93.33%、MRR 90.83%，未优于该方案。
参考（Reference）： [WeKnora MMR](https://github.com/Tencent/WeKnora/blob/bccb4b151bae403508da77fbb174efc79dc47c1a/internal/reranking/mmr.go) 的相关性与多样性取舍；适配为简单来源上限，并未实现完整 MMR 或复制 WeKnora。

## 3. 保留复合问题并区分流程询问与操作请求

问题（Problem）： 复合查询改写丢失所需来源；询问“请求人工”的流程被入口当作真实转人工命令。
改动（Change）： 首次会话的多问句保留完整原查询；通用意图规则区分第三人称流程问题与直接操作命令。
改动前（Before）： 一个恢复状态案例失败；两个多来源案例只覆盖一半；流程问题未进入检索。
改动后（After）： 相关复合案例 Task Success 与 Recall@5 均通过，工单流程回归通过；共享意图规则的离线回归通过。
原因（Why）： 不按 Case ID 分支，保持查询信息和当前用户的真实请求一致。
参考（Reference）： WeKnora Rewrite 的保留实体、条件、全部子问题与请求类型原则。

## 4. 有依据的回答与有效引用

问题（Problem）： 资料缺失被说成服务绝对不支持；回复遗漏已知流程和确认步骤；截短片段 ID 导致数据库错误。
改动（Change）： 回答覆盖各个子问题；资料不足时解释具体缺口、已知事实及确认下一步；生成引用限定为真实完整 ID，校验前过滤不在检索结果中的 ID。
改动前（Before）： 两个 No-Answer 案例都失败；一次定向执行出现无效 UUID 错误。
改动后（After）： 后续两组 No-Answer 定向回归均为 2/2；引用约束后的两组没有 UUID 执行错误。一次评分器复评有潜在语义误判，另行保留人工审阅提示。
原因（Why）： 让回答可核查，避免以泛化拒答代替资料中的真实边界和可行流程。

## 5. 有限次数的向量服务重试

问题（Problem）： Baseline 出现一次 Gemini 429，导致完整执行缺失。
改动（Change）： 使用已安装 Google SDK 的有限次数 429/5xx 重试。
改动前（Before）： Baseline 一个 Provider Error。
改动后（After）： 后续定向执行没有 Gemini Provider Error；正式结果以最终完整验证为准。
原因（Why）： 处理短暂限流，同时保留重试耗时与未恢复错误。

## Evaluator corrections

支持四种正式格式和来源组 AND/OR；实际前五名先截取再计分。表格按主题状态行，文本还接受相邻段落的主题证据，避免只认可一条原句。使用原始文件独立核查事实和引用，不把生产 Citation Validator 当答案。

定向测试发现 5 次证据编号/事实编号输出错误；约束编号并合并事实判定与证据后，只重跑 Judge，复用已有 Agent 答案。原始错误留在复评记录，不重新运行 Baseline Agent，也不修改标签。

另一次 Judge 多余拒答诊断字段通过原始响应离线重放修复，新增模型调用为零；原始判定保留，并指出其一条事实覆盖可能为误判。Baseline 的引用外最终断言统计也仅从已有证据重放，完整准确率在有 Provider Error 时保持 unknown。没有发现需要改动 Ground Truth 的确证冲突。

官方参考版本：Tencent/WeKnora `bccb4b151bae403508da77fbb174efc79dc47c1a`。已检查解析、分块、元数据、完整查询改写、Hybrid/RRF、Reranking 和 Wiki 的代码及文档；当前没有解析或知识组织瓶颈证据，保留现有 Loader、Chunking 和 PostgreSQL。

## 6. 最终验证中的修正

问题（Problem）： 首次完整验证为 25/30，出现条件/业务阶段遗漏、引用检查普通文本输出，以及两次评分错误；SLA 一条自动事实判断还存在潜在语义误判。
改动（Change）： 提醒完整列出操作前提、阻碍分支、核验与业务边界；明确引用检查 JSON 和完整索引；评分要求所有事实子句实际出现，缩短说明，并将缺证的草稿完整性判为未满足，而非阻断最终评分。
改动前（Before）： 相同 7 个选定案例在首次完整运行中 4/7，通过草稿评价的两个缺证/超长响应造成评分中断。
改动后（After）： 定向 5/7、0 Error，3 个回归案例全部通过；资料不足回复包含更多已知流程。完成边界遗漏和一次历史问题误拒答仍未解决。
原因（Why）： 修正有证据的通用问题；不继续围绕少量不稳定回答反复改动。只使用允许的一次额外完整 Development 验证，随后冻结 Agent 并运行一次 Holdout。

当时的完整运行预算：Baseline 一次；最终验证一次；因上述明确问题补充最终验证一次。本节记录时 Holdout 尚未运行，后续结果见下文及 [历史记录](../BENCHMARK_HISTORY.md)。

## 第一阶段开发集的停止结果

最终为 16/30；Recall@5 93.33%；MRR@5 90.83%；No-Answer 1/2；1 个格式错误导致的应用错误，0 Provider/Evaluation Error，0 Timeout。目标未达到。原 Baseline 的三个自动通过案例也存在来源审查疑点，原始判定保留。多轮定向检查及获准的一次额外完整验证没有证明能稳定达到 95%。剩余问题包括前提和边界覆盖不足、偶发错误转人工、引用和输出稳定性，以及同模型族 Judge 的局限。Agent 在 Holdout 前冻结，禁止从 Holdout 反向调优。

## 运行环境与阶段完成情况

最终 Development 遇到 51 次上游共享池 429，通过既有 Fallback 恢复，因此属于混合模型运行，不能作为单一主模型的直接对比。来源审查发现原 Baseline 三个自动通过案例仍有遗漏，原判定保留。不能把观察到的净变化全部归因于最终 Prompt 修改。

Holdout 仅运行一次，4/10；Recall@5 51.67%；MRR@5 63.33%；Answer Accuracy 40%；Unsupported Claim 4/28（14.29%）；No-Answer 1/2；Error/Timeout 0；恢复 16 次上游 429 调用。六个原始失败保留在 Holdout 报告中。该阶段 Holdout 后未修改 Agent/RAG。

整个第一阶段 RAG 任务新增 OpenRouter 实际费用 $0.07138368，计账费用 $0.07138368；已知 Tokens 1599438。未包含 Gemini 查询费用。范围包括 Baseline、两次优化后的完整运行、六轮定向运行及 Holdout。停止时未宣称达到 95% 目标。

## 第二阶段：回答流水线优化（仅 Development）

本阶段复用第一阶段已保存的 Development 回答；上面的历史 Holdout 未重跑，也未用于优化。Dataset、原 Ground Truth、23 份来源、Hybrid 检索与来源多样性保持不变。最终完整 Development 运行一次：`20261004T131851Z-51f8f0b8`。收到最终冻结指令后，只完成已有回答的离线评分和文档整理，未再修改正式代码、Prompt 或新增 Agent 测试。

### 7. 分开核对回答覆盖、证据支持与问题范围

问题（Problem）： 来源包含事实被误计成回答已经覆盖；缺少可选事实导致整例失败；重复证据编号中断评分。原 61/80 Fact Coverage 中存在没有回答证据的覆盖判定。
改动（Change）： 分别要求回答原文片段和原始来源支持；保留证明并规范证据编号；分开记录 Core 与 Optional，不重写原始事实。对保存的 Before/After 回答统一应用已记录的问题范围、等价表达与来源目录修正。正式 Rubric 保持冻结；独立报告 Rubric 保留全部 82 个事实 / 197 个子句，分为 137 Core / 60 Optional。
改动前（Before）： 原 Development Task Success 16/30（53.33%）；审计复用回答，没有新增 Agent 调用。十三个已完成的原失败包括五个 Core 缺失、八个仅 Optional 缺失案例。
改动后（After）： 同一批旧回答复核为 21/30（70.00%），这部分变化完全来自评分修正。最终完整运行保留自动成绩 19/30，另外记录离线复核成绩 26/30。十一个自动失败中七个属于评分问题，四个真实失败保留。严格复合覆盖和可选内容缺失仍可查阅。
原因（Why）： Judge 误判及复合事实的全有或全无评分，不能归功于 Agent 改进，也不能通过删除 Ground Truth 隐藏。最终复核还修正一个 SLA 假通过及来源目录遗漏的一行原始工作簿证据。

### 8. 原子声明与辅助定位片段

问题（Problem）： 长声明捆绑多个事实，一个条件前提或无关部分失败，就连带删除已有支持的其他事实；表格定位片段并不连续，却被当成硬性否决条件。
改动（Change）： 要求声明可独立核验，在验证前按句子边界拆分，并向验证器提供问题和历史。辅助定位片段只作为提示，保留实际 UUID、租户、版本、日期及必须执行的语义证据核验。所有通过的声明都进入最终组合。
改动前（Before）： 十条原始正确检索回答轨迹中，六条为 Generation Omission，四条为 Citation Validation Loss，其中两条属于 Claim Bundling。未观察到 Final Composition Loss。
改动后（After）： 采用同一最终问题范围审计，Correct Retrieval but Wrong Answer 从七例降至三例。stock-version、read-retryable、ticket-status 恢复。最终生成与引用阶段的结构化输出错误为零；engineer-recheck 和 SLA 仍有真实语义或引用失败。移除一个已证明的评分器误报后，最终 Unsupported Claims 为 2/125。
原因（Why）： 一个错误子声明或非逐字连续的表格定位片段，不应抹掉另一个有证据支持的事实；真实证据和业务阶段准确性仍是必要条件。

### 9. 一次有边界的完整性复核

问题（Problem）： 上下文已有用户要求的事实，但草稿只回答部分问题或引用错行；泛化的资料不足措辞可能丢失已知边界。
改动（Change）： 只根据问题、历史、上下文和草稿做一次复核，明确映射子问题、证据与草稿索引；补充遗漏或修正引用后，再核验每条声明。复核使用明确的低推理强度。Agent 永远不接收 expected_facts、评分 Rubric 或 Case ID。
改动前（Before）： 第二次定向运行为 10/15，Core 62/70，仍有遗漏。最终关注的七例在已保存 Baseline 回答中原为 2/7。
改动后（After）： 最后完成的定向草稿重放为 6/7；Core 29/31，No-Answer 2/2，Unsupported 0。同一最终审计下，完整 Development 为 26/30，旧回答为 21/30；Core 为 130/137，旧回答确认值为 119/137。七个旧未通过案例恢复，但 version-current 和 SLA 退化；No-Answer 从复核后的 2/2 降至 1/2。应用延迟 P50/P95 从 24.56/39.15 s 升至 55.45/80.32 s。
原因（Why）： 这是可审查轨迹的通用完整性机制，没有按案例分支。其费用和剩余遗漏都有实际影响，定向通过不能证明整套评测稳定。

### 10. 有限结构化输出恢复与真实供应商计账

问题（Problem）： 偶发格式错误导致应用或评分结果缺失；供应商 Fallback 和评分失败容易与回答逻辑混淆。低推理强度的复核需要匹配输出预算预留。
改动（Change）： 每个结构化阶段最多恢复一次，保留原始 JSON 修复、校验及独立阶段事件；未解决的供应商错误继续上抛。明确的低推理强度 6144-token 上限同时用于预检查与费用预留，不提高全局 $1 预算和 $0.9 停止线。保留供应商、Fallback 与最终错误次数。
改动前（Before）： 旧完整 Development 有一个未恢复的应用输出错误，恢复 51 次上游 429；channel-scope 没有最终回答。
改动后（After）： 最终 30/30 执行完成；Error/Provider/Evaluation Error/Timeout 均为零。十次结构化失败恢复（六次规划、四次 Judge），十二次上游失败通过 Fallback 恢复。channel-scope 通过。完整运行的 OpenRouter 应用加 Judge 实际费用为 $0.0292672238；最终离线复核新增 $0。Gemini 查询费用未包含。
原因（Why）： 恢复有限且可计量，不能把可靠性恢复全部解释为 Prompt 推理能力提高。最终运行仍然混合使用多个模型。

## 最终决定（Final Decision）

第二阶段停止结果：Task Success 86.67%；Core Coverage 94.89%；总体原子覆盖 72.59%；严格原 expected_facts 45/82；Recall@5 95.00%；MRR@5 90.83%。仍有三个正确检索但回答错误案例，以及一个元数据或空上下文失败。四个真实失败及两个 Baseline 已通过案例的退化，使 95% 和无退化目标均未达到。Agent 与评分代码冻结，该阶段未授权进一步运行或优化。详情和原始指标保留于本地 `reports/rag/answer-optimization/final-offline-review/final.md`，该证据目录不随仓库克隆。
