# Phase 1 / Task 01 完成报告

> 完成日期：2026-09-17  
> 状态：完成，等待用户确认后才能进入 Phase 2。  
> 事实来源：`V2/building-plan-V2.md`。

## 1. 完成内容

- 建立可独立运行的 V2 Python 项目、锁定依赖、PostgreSQL + pgvector Compose 环境和 `001_support.sql`。
- `V2/.env` 从 `V1/.env` 复制后由 V2 独立读取，已被 Git 忽略；模型名和 Key 均不写死。
- 建立 company、user、token、conversation、message、document、retrieval run、agent run 和 LangGraph checkpoint 的 Phase 1 最小数据模型。
- 导入 12 篇带版本和公司范围的测试产品资料，使用 Google Embedding 生成 1024 维向量；重复导入不会新增相同片段。
- 接通 Customer Agent 的真实终端问答、向量检索、Groq Structured Output、引用显示、会话继续和持久化。
- 建立确定性 Model Router：Customer RAG 等轻量任务选择 Groq；复杂调查和 Tool Calling 类任务预留为 Google。Phase 1 只验证 Google 能力，不实现 Support Agent 或业务 Tool。
- 接通 LangSmith 对 Conversation、Graph Node、Embedding、Retrieval 和 LLM 的真实追踪。

Phase 1 没有实现 Support Agent、Handoff、订单/发货/库存后台、Action、Approval、Ticket、完整 API 或 React UI。

## 2. 主要文件

- 运行与依赖：`pyproject.toml`、`requirements.lock`、`.env.example`、`.gitignore`、`compose.yaml`
- 应用：`app/config.py`、`app/db.py`、`app/auth.py`、`app/models.py`、`app/docs.py`、`app/customer.py`、`app/graph.py`、`app/trace.py`、`app/cli.py`
- 数据与初始化：`db/001_support.sql`、`lab/bootstrap.py`
- 产品资料与提示词：`docs/product/`、`docs/sources.md`、`docs/versions.md`、`prompts/customer.md`
- 测试与开发集：`tests/`、`evals/dev.jsonl`、`evals/price_config.json`
- 当前运行说明：`README.md`

## 3. 当前真实数据流

```text
CLI 中文问题与本地 token
  → 普通代码映射 user/company 并校验 conversation scope
  → Google Embedding 生成 1024 维问题向量
  → PostgreSQL + pgvector 按 company 和资料有效期检索
  → LangGraph retrieve node → answer node
  → Groq 根据可见片段生成结构化中文回答
  → 普通代码只接受真实命中的 chunk ID 并生成引用
  → PostgreSQL 保存消息、检索记录、模型用量、Trace ID 和 checkpoint
  → CLI 显示 conversation、answer、chunk title/ID/source 和 token usage
```

使用 `--conversation` 时，普通代码先重新核对用户与公司的会话范围，再加载历史消息并执行同一条 Customer Graph。

## 4. 测试与验证结果

- `python -m pytest -q`：9 项通过。
- `python -m ruff check app lab tests`：通过。
- 测试会创建隔离的空数据库、运行 migration，并在结束后删除；不会清空 V2 主开发数据库。
- 资料导入实测：首次找到并导入 12 篇，第二次导入 0 篇并跳过 12 篇。
- 真实开发集：6 个问题全部调用模型完成；另完成 1 次同会话追问。
- 当前主数据库证据：12 个资料片段、1024 维向量、6 个会话、14 条消息、7 个成功 Customer Agent Run、7 个带引用回答、7 个 Retrieval Run、100 个 checkpoint。
- 7 次 Customer 回答共记录 8,915 tokens，无缺失 usage；当前没有可靠费用映射，因此费用记为 unknown，不记为零。
- 安全验证：无效 token 被拒绝；B 公司不能读取 A 公司资料或会话；配置调试输出不显示连接串或 API Key；V2 运行代码不存在对 `V1/` 的 import、路径或配置依赖。
- 收尾时首次回归因本地数据库容器未运行而在测试初始化阶段连接超时；执行现有 `docker compose up -d db` 后，同一测试集全部通过。没有把环境失败记录成代码成功。

## 5. LangSmith Trace

- 已生成真实 Trace。
- 数据库中 7 个成功 Agent Run 均保存了 Trace ID，收尾检查时 7/7 可由 LangSmith API 查询。
- Trace 覆盖 Customer conversation、LangGraph 节点、Google Embedding、pgvector Retrieval 和 Groq LLM。
- Trace、日志和报告不记录 API Key、Authorization Header、测试 token 或 Hidden Chain of Thought。

## 6. 模型使用

| 用途 | Provider / 模型 | 原因 |
| --- | --- | --- |
| Customer RAG 最终回答 | Groq / `openai/gpt-oss-20b` | 属于普通问答与轻量 Structured Output，符合确定性路由规则 |
| Phase 1 Structured Output、Tool Calling 与 Parallel Tool Calling 能力检查 | Google / `gemini-3.8-flash` | 同一轮真实返回两个互不依赖的只读 Tool Calls；未绑定给 Customer Agent |
| 文档与问题向量 | Google / `gemini-embedding-001` | 真实生成并验证 1024 维向量，供 pgvector 检索 |

所有模型名称均从 `V2/.env` 读取，没有写死在代码中。

补充能力验证：Google 模型同一响应返回 `ReadShopStatus` 与 `ReadPlatformStatus` 两个独立只读 Tool Calls，`tool_call_count = 2`。模型 API 元数据记录的上下文能力为 1,048,576 输入 tokens 与 65,536 输出 tokens。验证期间两次遇到临时 `503 high demand`，后续有限重试成功；失败没有记作通过。

## 7. 边界与已知问题

- 当前只有 Vector Search；Keyword/BM25、RRF、Reranking 和完整 Citation Validation 属于 Phase 2。
- 当前引用只允许来自实际检索且处于当前公司范围的 chunk；尚未进行 Phase 2 的语义 claim support 校验。
- Google 能力检查曾遇到临时 `503 high demand`，有限重试后成功。外部模型可用性和地域仍是本地运行条件。
- Phase 1 的成本为 unknown；项目没有用不可靠价格推算或把未知费用写成零。
- `V1/.gitignore` 在 Phase 0 前已经是修改状态；其 SHA-256 仍为 Phase 0 记录的值，Phase 1 没有修改、删除、移动或重命名任何 V1 文件。

## 8. 下一阶段

Phase 2 / Task 02–03 将在用户确认后扩展现有入口：增加 Query Rewrite、中文 Keyword/BM25、Vector + Keyword、RRF、Reranking 和 Citation Validation，并比较 `vector_only`、`hybrid`、`hybrid_rerank`。完整 API 和 React UI 仍保留到 Phase 8。

在收到用户确认前不进入 Phase 2。
