# Phase 9 准备阶段已验证版本

验证日期：2026-09-18

## 运行环境

- Python：3.13.14
- Docker Server：29.6.2
- PostgreSQL/pgvector image：`pgvector/pgvector:pg17`
- 向量维度：1024

## 核心依赖

- LangChain：1.4.1
- LangGraph：1.2.11
- LangGraph PostgreSQL Checkpoint：3.1.2
- LangSmith：0.12.6
- langchain-groq：1.1.3
- langchain-google-genai：4.3.7
- google-genai：1.75.0
- psycopg：3.3.5
- pgvector：0.5.0
- jieba：0.42.1
- rank-bm25：0.2.2
- transformers：4.57.6
- torch：2.14.0
- FastAPI：0.141.1
- HTTPX：0.28.1
- Uvicorn：0.53.0
- React / React DOM：19.3.0
- Vite：8.3.0
- Playwright：1.63.0

完整可复现依赖见 `requirements.lock`。

## 实际模型

- Groq 普通问答/Customer RAG：`openai/gpt-oss-20b`
- Google 能力检查：`gemini-3.8-flash`
- Google Support 临时错误 fallback：`gemini-3.6-flash`
- Google Embedding：`gemini-embedding-001`，请求输出 1024 维
- 本地 Reranker：`Qwen/Qwen3-Reranker-0.6B`

模型名称全部从 `V2/.env` 读取，代码没有写死。Phase 1 已真实验证 Groq Structured Output、Google Structured Output、Google Tool Calling、Google Parallel Tool Calling 和 1024 维 Embedding。

Google Parallel Tool Calling 的最小真实验证在同一轮请求两个互不依赖的只读测试工具，响应返回 2 个 Tool Calls：`ReadShopStatus` 与 `ReadPlatformStatus`。当前 Google 模型 API 元数据记录 1,048,576 输入 tokens 和 65,536 输出 tokens。能力检查曾出现临时 `503 high demand`，有限重试后成功；没有将失败当成空结果或假成功。

Phase 4 Support 调查继续以 `GOOGLE_MODEL` 为主。普通代码只在有限重试后仍为临时 `503 UNAVAILABLE/high demand` 时读取 `GOOGLE_FALLBACK_MODEL`；权限、配额、输入或输出质量问题不会触发切换。2026-09-17 的真实验收尝试记录了 `gemini-3.8-flash → gemini-3.7-flash`，两者当次均返回临时 503，运行被如实保存为 failed。随后按验证要求将临时 fallback 改为 `gemini-3.6-flash`。同期 LangSmith 返回月度 unique traces 配额已用尽的 429，新 Trace 未成功上传，因此 Phase 4 的远端 Trace 验收延期到 Phase 9/最终验收。

`gemini-3.6-flash` 修改后只执行了一次最小真实 Support Tool Calling 验证，并在同一响应中正确返回一个 `GetOrder` Tool Call。该验证关闭 LangSmith 上传，没有继续运行完整调查或重复 capability check。

## Phase 5 业务运行

- Phase 5 的 Proposal、Policy、Approval、Execution 与 Verification 不调用 LLM。
- PostgreSQL 新增 Action、Decision、Execution、Verification、Step、Merchant repair receipt 与 recovery task 表；仍使用同一数据库实例和明确业务 schema。
- 本地真实 `O-P5-REAL-1` 案例经过 Platform、Merchant HTTP repair contract、独立 Worker 和双端读回后得到 `verified_resolved`；重复批准仍只有一个 decision、receipt 和 merchant order。
- LangSmith 远端上传因月度配额 429 保持关闭；本地保存 action/request/receipt/Evidence/step/error 与可用 trace metadata。

## Phase 6 业务运行

- 新增 Warehouse 服务和 `db/004_shipments.sql`；Platform、Merchant、Warehouse 和独立 Worker 使用同一 PostgreSQL instance 中各自的业务 schema。
- `O-PH6-HTTP-0918` 真实 HTTP/Worker 案例完成 Platform order → Merchant order → Warehouse order/shipment → Merchant shipment → Platform shipment，三端 tracking number 一致且 Warehouse shipment count 为 1。
- `O-SHIPMENT-RESPONSE-LOST` 真实 Compose 案例在 Platform transaction 提交后延迟 6 秒；Worker 先得到未知结果，再通过 Platform 查询对账为 `reconciled: true`。重复恢复后 Warehouse、Merchant、Platform 均只有一条 Shipment。
- Order response-lost、Shipment response-lost、实际子进程终止、lease 接管、无批准不写、版本变化阻止执行和跨公司 Shipment 查询均有 deterministic regression test。
- Phase 6 最终本地回归为 58 项通过，Ruff 通过；测试从空数据库运行全部 migration。
- Phase 6 没有调用 Groq 或 Google。容器恢复验收明确以 `LANGSMITH_TRACING=false` 运行；三条真实 LLM Shipment 调查和远端 LangSmith Trace 标记为 **External Acceptance Deferred**，留到 Phase 9。

## Phase 7 业务运行

- 新增 `db/005_stock.sql`，在同一 PostgreSQL instance 内保持 Warehouse、Merchant、Platform 的库存数据所有权。
- Order、Shipment 和 Stock Worker 统一处理 401、403、429、500、503 与 timeout；失败记录包含 company/shop、operation、error code、HTTP status 和 request ID。
- 真实 Compose 案例中，`shop-a / O-PH7-500-0918` 因受控 500 失败且没有 Merchant order；`shop-b / O-PH7-OK-0918` 独立完成。
- 真实 Compose 库存案例完成 Warehouse `80/10` → Merchant safety `5` → Platform `65`，来源版本为 1。
- Support 新增只读 `GetStockFacts`，没有 connection restore 或 stock write tool；版本、时间窗口和信息完整性由普通代码判断。
- Support budgets 可通过 `SUPPORT_MAX_TOOL_CALLS`、`SUPPORT_MAX_INVESTIGATION_MS`、`SUPPORT_MAX_CONSECUTIVE_ERRORS`、`SUPPORT_MAX_TOOL_ERRORS` 配置。
- 冻结 30 条 `evals/holdout.jsonl`；SHA-256 记录在 `evals/holdout-summary.json`，完整 Benchmark 留到 Phase 9。
- Phase 7 完整回归 80 项通过，Ruff 通过；Compose 验收显式关闭 LangSmith，没有调用 Groq 或 Google。真实模型与远端 Trace 标记为 **External Acceptance Deferred**。

## Phase 8 业务运行

- 新增 `db/006_tickets.sql`、受控 Ticket policy、确定性 assignment rule 和显式 Engineer read grant；同一会话只能存在一个 open/in-progress Ticket。
- 新增 FastAPI `/api/v1` 与 OpenAPI 2.0.0 contract，以及 React/Vite 最小演示页；CLI/API/UI 复用 `backend.app.conversations.process_conversation_message` 和现有 Action/Ticket 函数。
- 前端使用 React 内建 state 和 fetch，没有 customer selector、Agent toggle、额外状态管理框架或大型 Design System。
- 前端依赖由 `frontend/package-lock.json` 冻结；Phase 8 production build 已通过。
- Phase 8 完整回归 88 项通过，Ruff 通过；进入 Phase 9 前的最小 Playwright E2E 使用一次真实 Groq Query Plan，将会话切换到 Support，并验证 Ticket 创建和授权 Engineer 读取。E2E 显式关闭 LangSmith，没有调用 Google。

## Phase 9 准备阶段

- `evals/dev.jsonl` 已整理为 50 个开发案例；冻结 Holdout 保持 30 个且 SHA-256 与摘要一致。
- `evals/run.py`、`lab/checks.py`、`app/report.py` 已建立 Dataset 校验、运行计划、失败保留、独立检查、用量聚合和成本估算。
- 三类对照运行数固定为 Retrieval 48、Investigation 48、Roles 80，共 176；Holdout 另为 90 次。
- 3 个案例的 validate-only Dry Run 通过，0 个模型调用、0 个外部调用、3 个结果均为 `not_scored`。
- 正式 Benchmark 未运行；full mode 在用户确认 USD 3.70 预算建议前保持锁定。

## Phase 10 本地交付

- 新增 `db/007_ticket_rechecks.sql`，持久化 `RESOLVED`、`UNRESOLVED` 和 `NEEDS_INFO` 复查结果、Evidence IDs、执行工程师和时间。
- 订单 Ticket 复用现有订单 Verification 条件；发货 Ticket 复用现有三端 Verification 条件；库存 Ticket 复用现有公式、版本和传播时间判断。
- 只有确定性检查通过才关闭 Ticket。结果未满足或信息不足时保持打开；已关闭 Ticket 的后续失败复查会重新打开。
- API 新增 Ticket recheck 和 HTML export；CLI 新增 `ticket recheck` 与 `ticket export`；React 工程师视图新增复查按钮和关闭状态展示。
- Compose 新增独立一次性 `init` 服务。API 镜像不包含 Lab、Eval、Holdout 或 V1；业务服务和 API 对 `.local` token 目录保持只读。
- 当前测试策略取消付费 Benchmark、Holdout 重复运行、批量真实 Groq/Google 调用和远端 LangSmith Trace。Phase 10 本地验收全部使用 `LANGSMITH_TRACING=false` 和模型占位 Key。

## Phase 3 业务运行

- `platform`、`merchant` 和 `worker` 使用同一个最小 Dockerfile 构建，业务服务只复制运行必需模块；容器只有数据库和 LangSmith 配置，不注入 Groq/Google Key。
- Platform 与 Merchant 通过容器网络上的真实 HTTP 调用连接；Worker 独立轮询 PostgreSQL 任务。
- Phase 3 没有调用 Groq、Google Chat 或 Embedding 模型，也没有注册 Support Agent Tool。所有订单安全与状态判断由普通代码和数据库约束完成。
- 业务调用会创建 LangSmith-compatible Trace ID，并关联到 delivery、receipt 和 worker task。GCP US endpoint、`resolveai-v2` 项目以及 Platform/Merchant/Worker 三类远端 Trace 已实际查询成功；当前 Key 不需要额外 Workspace ID。
