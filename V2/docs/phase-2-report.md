# Phase 2 / Task 02–03 完成报告

> 完成日期：2026-09-17  
> 状态：完成，等待用户确认后才能进入 Phase 3。  
> 事实来源：`V2/building-plan-V2.md`。

## 1. 完成内容

- Customer Graph 增加 Groq 查询规划、最多一次 Query Rewrite、必要澄清和 Handoff Intent；Customer Agent 仍没有后台业务 Tool。
- 检索先按 company、product、version 和有效期过滤，再运行 Google Vector Search 与内存中文 BM25；两路各取最多 15 个候选，经 RRF 合并为最多 20 个。
- `hybrid_rerank` 使用本地真实 `Qwen/Qwen3-Reranker-0.6B` 将融合候选重排为前 5 个，没有用 Google 主模型冒充 Reranker。
- 支持 `vector_only`、`hybrid`、`hybrid_rerank` 三种真实模式，并把过滤条件、两路排名、融合排名、重排排名、模型、延迟和错误保存到 PostgreSQL。
- 回答拆成可核查 Claims。普通代码先复查引用存在性、公司范围、版本、有效期和本次检索集合，再由 Groq 检查 Claim Support；不支持或检查失败的结论不会作为确定答案输出。
- 产品资料从 12 篇扩展为 14 篇，增加渠道 A/B 差异和已过期 1.0 版本；开发问答案例从 6 个扩展为 10 个。
- 修复 Windows CLI UTF-8 输出；具体后台状态只返回中文 Handoff Intent，不虚构已完成转交或后台调查。

## 2. 主要文件

- 检索与重排：`app/docs.py`
- 查询规划与回答：`app/customer.py`、`app/graph.py`、`prompts/customer.md`
- 引用检查：`app/citations.py`、`prompts/citation_check.md`
- 数据记录：`db/001_support.sql`、`app/models.py`、`app/cli.py`
- 新资料：`docs/product/13-channel-differences.md`、`docs/product/14-legacy-order-sync.md`
- 测试：`tests/test_docs.py`、`tests/test_citations.py`、`tests/test_customer.py`、现有访问测试
- 配置与交付：`pyproject.toml`、`requirements.lock`、`.env.example`、`README.md`、`docs/versions.md`

## 3. 当前真实数据流

```text
CLI 问题 + token + retrieval mode
  → 普通代码校验 user/company/conversation scope
  → Groq Query Plan
      ├─ 缺少会影响资料选择的版本 → Clarification
      ├─ 需要真实后台状态 → Handoff Intent（不查询后台、不切换角色）
      └─ 产品问题 → 最多一次 Query Rewrite
  → company/product/version/effective-date 过滤
  → Google Embedding + pgvector Top 15
  → 中文 BM25 Top 15（Hybrid 模式）
  → RRF 合并最多 20
  → Qwen3 Rerank Top 5（Hybrid Rerank 模式）
  → Groq 生成 Claims + cited chunk IDs
  → 普通代码校验 citation existence/scope/version/effective date
  → Groq 检查 Claim Support
  → 删除无依据结论或安全返回需要进一步支持
  → 保存 message、retrieval ranks、usage、Trace ID、checkpoint
  → CLI 显示回答、引用、会话和 Handoff Intent
```

## 4. 测试与真实验证

- `python -m pytest -q`：19 项通过。
- `python -m ruff check app lab tests`：通过。
- 空测试数据库可运行更新后的 migration；Phase 1 回归继续通过。
- 覆盖：公司隔离、过期版本先过滤、错误码分词、RRF、Reranker 故障降级记录、伪造引用、跨公司引用、语义不支持结论、最多一次改写、版本澄清和 Handoff Intent 安全文案。
- 三种 CLI 模式真实运行 `ORDER_SYNC_DISABLED`：都返回 `docs/product/08-error-codes.md`，并通过真实 Groq Claim Support 检查。
- 真实 Qwen3 Rerank：数据库内 6/6 次 `hybrid_rerank` 有非空排名且 `rerank_error IS NULL`。
- 4 个小型开发案例对照：三种模式均为 4/4 Top-5 命中；Vector Only 平均 897 ms，Hybrid 平均 949 ms，Hybrid + Qwen3 Rerank 热运行平均 5,703 ms。Vector Only 在 4 案例中均将预期资料排第一；Hybrid 有一例排第三，Rerank 有一例排第二。
- 数据库包含 14 个片段，其中 13 个当前有效；过期 1.0 资料不会参与当前排序。
- 真实澄清案例返回版本号追问；真实具体订单案例只记录 Handoff Intent，并明确未读取后台或完成转交。

当前小样本下 `vector_only` 质量没有更差且延迟最低，因此暂定为默认模式。另两种模式保留为真实可运行选项，后续数据增加后重新评估。费用：Google/Groq 用量按已有 token 记录；本地 Qwen3 没有外部调用费用，未建立硬件成本估算，因此不写零成本结论。

## 5. LangSmith Trace

- 最新抽查的 8 个成功 Agent Run 均保存 Trace ID，8/8 可由 LangSmith API 查询。
- Trace 覆盖 Query Plan、Embedding、Vector/BM25/RRF、Qwen3 Rerank、Customer Answer、Citation Claim Support 和 LangGraph 节点。
- PostgreSQL Retrieval Run 可关联 mode、过滤条件、各阶段排名、Rerank 延迟/错误和 Trace ID。
- 不记录 API Key、Authorization Header、测试 token 或 Hidden Chain of Thought。

## 6. 模型使用

| 用途 | Provider / 模型 | 原因 |
| --- | --- | --- |
| Query Plan、一次 Rewrite、Customer Claims、Claim Support | Groq / `openai/gpt-oss-20b` | 普通分类、简单 Structured Output 和 Customer RAG，符合确定性路由 |
| 文档与问题向量 | Google / `gemini-embedding-001` | 已验证 1024 维向量，与 pgvector schema 一致 |
| 候选重排 | 本地 / `Qwen/Qwen3-Reranker-0.6B` | building plan 指定的真实 Reranker；不增加第二个向量数据库或远端服务 |
| Parallel Tool Calling 补充验证 | Google / `gemini-3.8-flash` | 同轮返回两个独立只读 Tool Calls；没有接入 Customer Agent |

Google 模型上下文 API 元数据：1,048,576 输入 tokens、65,536 输出 tokens。

## 7. 已知问题与边界

- 4 个检索案例和 14 个片段只用于开发选择，不能证明大规模语料质量或性能。
- Qwen3 Reranker 首次运行需要下载模型；数据库中含冷启动的平均 Rerank 延迟约 26.6 秒，缓存后 4 案例平均约 5.7 秒，CPU 演示可用但不是低延迟路径。
- Citation Claim Support 是降低风险的语义检查，仍可能误判；检查失败时系统删除结论或返回不确定，不宣称保证正确。
- Google 能力检查先遇到 `503 high demand`，随后真实并行调用成功；之后完整 doctor 又遇到当日免费配额 `429`。已成功的 Parallel Tool Calling 事实不受影响，但外部配额仍是运行条件。
- Handoff 仍只是意图；Conversation 角色切换和 Support Agent 属于 Phase 4。完整 API/React UI 仍属于 Phase 8。

## 8. 下一阶段

Phase 3 / Task 04 将在用户确认后实现最小订单业务模拟器：平台保存源订单，通过真实 HTTP Contract 进入商家接收记录和后台任务，再产生商家订单；同时覆盖同步关闭、重复事件和冲突。不会提前实现 Phase 4 的 Support 调查。

在收到用户确认前不进入 Phase 3。
