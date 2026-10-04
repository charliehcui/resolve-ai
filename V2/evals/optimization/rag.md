# RAG Optimization

固定 30 Development / 10 Holdout；问题、标签和 23 份来源保持冻结。只记录最终保留的重要优化。

## 1. Query scope and intent

Problem: 公司名被当作产品条件；文档中的流程描述被误当作实际后台请求。
Change: 明确区分租户和产品，用登录公司的标识校正错误产品字段；保留完整查询条件并优先检索规则问题。
Before: 首组选定案例 3/8 通过（其中 1 个 Embedding Provider Error）。
After: 相同 8 个 Agent 答案复用复评后 6/8 通过，3 个已通过的回归案例全部保持通过。
Why: 修复访问范围和意图判断，不依赖 Case ID 或答案。
Reference: [WeKnora Query Understanding](https://github.com/Tencent/WeKnora/blob/bccb4b151bae403508da77fbb174efc79dc47c1a/config/prompt_templates/rewrite.yaml)：保留原问题的实体、条件和请求类型；适配为现有查询规划器的指引与登录范围校验。

## 2. Hybrid and context selection

Problem: 原问题在正确范围内仍有关键词排序和多来源覆盖不足；相同文件的多个片段占满前五名。
Change: 保留现有 BM25、Vector 和 RRF；采用 Hybrid，在候选池中优先选每个来源最多两个片段，保留第一名，来源不足时补满。
Before: 相同 30 个缓存查询，Vector Recall@5 91.67%、MRR@5 82.44%；原 Hybrid 91.67%、94.17%。
After: Hybrid 加来源覆盖后 Recall@5 96.67%、MRR@5 94.17%，没有检索退化；重放检索未再次调用模型。
Why: 小改动解决来源集中，避免 CPU Reranker 的额外延迟；原 Reranker Recall 93.33%、MRR 90.83%，未优于该方案。
Reference: [WeKnora MMR](https://github.com/Tencent/WeKnora/blob/bccb4b151bae403508da77fbb174efc79dc47c1a/internal/reranking/mmr.go) 的相关性与多样性取舍；适配为简单来源上限，并未实现完整 MMR 或复制 WeKnora。

## 3. Preserve compound questions and distinguish requests

Problem: 复合查询改写丢失所需来源；询问“请求人工”的流程被入口当作真实转人工命令。
Change: 首次会话的多问句保留完整原查询；通用意图规则区分第三人称流程问题与直接操作命令。
Before: 一个恢复状态案例失败；两个多来源案例只覆盖一半；流程问题未进入检索。
After: 相关复合案例 Task Success 与 Recall@5 均通过，工单流程回归通过；共享意图规则的离线回归通过。
Why: 不按 Case ID 分支，保持查询信息和当前用户的真实请求一致。
Reference: WeKnora Rewrite 的保留实体、条件、全部子问题与请求类型原则。

## 4. Grounded answer and citation validity

Problem: 资料缺失被说成服务绝对不支持；回复遗漏已知流程和确认步骤；截短片段 ID 导致数据库错误。
Change: 回答覆盖各个子问题；资料不足时解释具体缺口、已知事实及确认下一步；生成引用限定为真实完整 ID，校验前过滤不在检索结果中的 ID。
Before: 两个 No-Answer 案例都失败；一次定向执行出现无效 UUID 错误。
After: 后续两组 No-Answer 定向回归均为 2/2；引用约束后的两组没有 UUID 执行错误。一次评分器复评有潜在语义误判，另行保留人工审阅提示。
Why: 让回答可核查，避免以泛化拒答代替资料中的真实边界和可行流程。

## 5. Bounded embedding retries

Problem: Baseline 出现一次 Gemini 429，导致完整执行缺失。
Change: 使用已安装 Google SDK 的有限次数 429/5xx 重试。
Before: Baseline 一个 Provider Error。
After: 后续定向执行没有 Gemini Provider Error；正式结果以最终完整验证为准。
Why: 处理短暂限流，同时保留重试耗时与未恢复错误。

## Evaluator corrections

支持四种正式格式和来源组 AND/OR；实际前五名先截取再计分。表格按主题状态行，文本还接受相邻段落的主题证据，避免只认可一条原句。使用原始文件独立核查事实和引用，不把生产 Citation Validator 当答案。

定向测试发现 5 次证据编号/事实编号输出错误；约束编号并合并事实判定与证据后，只重跑 Judge，复用已有 Agent 答案。原始错误留在复评记录，不重新运行 Baseline Agent，也不修改标签。

另一次 Judge 多余拒答诊断字段通过原始响应离线重放修复，新增模型调用为零；原始判定保留，并指出其一条事实覆盖可能为误判。Baseline 的引用外最终断言统计也仅从已有证据重放，完整准确率在有 Provider Error 时保持 unknown。没有发现需要改动 Ground Truth 的确证冲突。

官方参考版本：Tencent/WeKnora `bccb4b151bae403508da77fbb174efc79dc47c1a`。已检查解析、分块、元数据、完整查询改写、Hybrid/RRF、Reranking 和 Wiki 的代码及文档；当前没有解析或知识组织瓶颈证据，保留现有 Loader、Chunking 和 PostgreSQL。

## 6. Final validation correction

Problem: 首次完整验证为 25/30，出现条件/业务阶段遗漏、引用检查普通文本输出，以及两次评分错误；SLA 一条自动事实判断还存在潜在语义误判。
Change: 提醒完整列出操作前提、阻碍分支、核验与业务边界；明确引用检查 JSON 和完整索引；评分要求所有事实子句实际出现，缩短说明，并将缺证的草稿完整性判为未满足，而非阻断最终评分。
Before: 相同 7 个选定案例在首次完整运行中 4/7，通过草稿评价的两个缺证/超长响应造成评分中断。
After: 定向 5/7、0 Error，3 个回归案例全部通过；资料不足回复包含更多已知流程。完成边界遗漏和一次历史问题误拒答仍未解决。
Why: 修正有证据的通用问题；不继续围绕少量不稳定回答反复改动。只使用允许的一次额外完整 Development 验证，随后冻结 Agent 并运行一次 Holdout。

完整运行预算：Baseline 一次；最终验证一次；因上述明确问题补充最终验证一次。Holdout 尚未运行。正式最终指标在 BENCHMARK_HISTORY.md 和三阶段报告中记录。

## Development stopping result

Final: 16/30; Recall@5 93.33%; MRR@5 90.83%; No-Answer 1/2; 1 malformed-output application error, 0 Provider/Evaluation Error, 0 Timeout. Target not met. The three original Baseline passes also have source-review concerns, with raw automated verdicts preserved. Multiple targeted rounds and the permitted extra full validation did not establish stable 95% performance. Remaining issues: incomplete prerequisite/boundary coverage, occasional wrong handoff, citation/output stability and same-family Judge limitations. Agent frozen before Holdout; do not tune from Holdout.

## Runtime context and completion

Final Development encountered 51 upstream shared-pool 429 responses, recovered by the existing fallback; this is a mixed-model run, not a clean primary-model comparison. Source review found omissions in the three original automated Baseline passes; raw verdicts are preserved. No reliable net improvement can be attributed solely to the final prompt change.

Holdout: one run, 4/10; Recall@5 51.67%; MRR@5 63.33%; Answer Accuracy 40%; Unsupported Claim 4/28 (14.29%); No-Answer 1/2; Error/Timeout 0; 16 recovered upstream 429 calls. Six original failures remain in the Holdout report. No Agent/RAG modification after Holdout.

New OpenRouter cost for the whole RAG task: $0.07138368 actual, $0.07138368 accounted; 1599438 known tokens. Gemini query fees excluded. Baseline, two optimized full runs, six targeted rounds and Holdout included. Stop without claiming the 95% target was achieved.

## Stage 2 — Answer Pipeline Optimization (Development only)

This stage reuses the saved Stage 1 Development answers; the historical Holdout above was neither rerun nor used for optimization. Dataset, original Ground Truth, 23 sources, Hybrid retrieval and source diversity are unchanged. One final full Development run: `20261004T131851Z-51f8f0b8`. Following the final freeze instruction, only saved-answer offline scoring and documentation were completed; no production code, prompts or new Agent tests changed.

### 7. Separate answer coverage, evidence support and question scope

Problem: Source facts were credited as if stated in the answer; missing optional facts failed whole cases; repeated evidence IDs interrupted scoring. The original 61/80 Fact Coverage included unsupported coverage verdicts.
Change: Require literal answer fragments independently from original-source support; preserve proofs and normalize evidence IDs; record Core and Optional separately without rewriting original facts. Apply documented final offline scope/equivalence/source-catalog corrections to both saved before/after answers. The production rubric remains frozen; the separate report rubric retains all 82 facts / 197 clauses, with 137 Core / 60 Optional.
Before: Original Development Task Success 16/30 (53.33%); audit reused answers, not Agent calls. Thirteen completed original failures included five Core-missing and eight Optional-only cases.
After: Same old answers score 21/30 (70.00%); this gain is evaluator-only. Final full run preserves automated 19/30 and separately records offline-adjudicated 26/30. Seven of its eleven automatic failures are scoring-only; four true failures remain. Strict compound coverage and missing optional content remain visible.
Why: Judge fallibility and all-or-nothing compound scoring must not be attributed to Agent improvement or hidden by deleting Ground Truth. Final adjudication also corrects an SLA false pass and an omitted original workbook evidence row.

### 8. Atomic claims and robust auxiliary anchors

Problem: A long bundled claim lost supported siblings when a conditional premise or unrelated part failed; formatted table anchors were noncontinuous and hard-vetoed genuinely supported claims.
Change: Ask for independently testable claims and split sentence boundaries before validation; give the validator the question/history. Treat auxiliary anchors as hints, retaining actual UUID, tenant/version/date checks and mandatory semantic evidence validation. Compose all accepted claims.
Before: Ten original correct-retrieval answer traces: six Generation Omission, four Citation Validation Loss, including two Claim Bundling losses. No Final Composition Loss was observed.
After: Under the same final question-scoped audit, Correct Retrieval but Wrong Answer falls from seven to three. Stock-version, read-retryable and ticket-status recover. Final generation/citation structured-output errors are zero; genuine semantic/citation failures remain in engineer-recheck and SLA. Unsupported final claims are 2/125 after removal of one proven evaluator false flag.
Why: A mistaken subclaim or nonliteral table anchor should not erase a supported sibling. Real evidence and business-stage accuracy remain necessary.

### 9. One bounded completeness review

Problem: Context already held requested facts while the draft answered only part of the question or cited the wrong retrieved row; generic no-answer wording could lose known boundaries.
Change: Perform one question/history/context/draft-only review, explicitly mapping subquestions to evidence and draft indices; add missing answers or correct citations, then validate every resulting claim. Use explicit low reasoning for this review. Never provide expected_facts, evaluator rubric or Case IDs to the Agent.
Before: Second targeted run was 10/15; Core 62/70, with unresolved omissions. The seven final focused cases previously scored 2/7 on saved baseline answers.
After: The last completed targeted saved-draft replay scored 6/7; Core 29/31, No-Answer 2/2, Unsupported 0. Full Development under the same final audit scores 26/30 versus old 21/30, Core 130/137 versus old confirmed 119/137. Seven old nonpasses recover, but version-current and SLA regress; No-Answer falls from audited 2/2 to 1/2. Application latency P50/P95 rises from 24.56/39.15 s to 55.45/80.32 s.
Why: This is a generic completeness mechanism with reviewable traces, not a per-case rule. Its cost and remaining omissions are material; targeted success did not establish full-suite stability.

### 10. Bounded structured output and honest provider accounting

Problem: Occasional malformed output lost application/evaluation results; provider fallback and evaluation failures could be confused with answer logic. A low-reasoning review needed a matching output-budget reservation.
Change: At most one recovery per structured stage, with raw JSON repair/validation and separate stage events; propagate unresolved provider errors. Match explicit low-reasoning 6144-token output limits to preflight/reservation without raising the global $1 budget or $0.9 stop. Retain provider/fallback and terminal error counts.
Before: Old full Development had one unrecovered application output error and 51 recovered upstream 429 responses; channel-scope had no final answer.
After: Final 30/30 executions complete; Error/Provider/Evaluation Error/Timeout all zero. Ten structured failures recover (six planning, four Judge); twelve upstream failures recover through fallback. Channel-scope passes. Full-run OpenRouter actual application+Judge cost is $0.0292672238; offline final review adds $0. Gemini query fees excluded.
Why: Recovery is finite and measurable; reliability gains cannot be reported as prompt-only reasoning gains. The final run remains a mixed-model execution.

Stage 2 stopping result: Task Success 86.67%; Core Coverage 94.89%; Overall atomic Coverage 72.59%; strict original expected_facts 45/82; Recall@5 95.00%; MRR@5 90.83%; three correct-retrieval wrong answers plus one metadata/empty-context failure. Four genuine remaining failures and two baseline-pass regressions prevent claiming the 95% or no-regression goal. Agent and evaluator code are frozen, and no further run or optimization is authorized in this stage. Details and unchanged raw metrics: [final report](../../reports/rag/answer-optimization/final-offline-review/final.md).
