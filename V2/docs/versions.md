# Phase 3 已验证版本

验证日期：2026-09-17

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

## Phase 3 业务运行

- `platform`、`merchant` 和 `worker` 使用同一个最小 Dockerfile 构建，业务服务只复制运行必需模块；容器只有数据库和 LangSmith 配置，不注入 Groq/Google Key。
- Platform 与 Merchant 通过容器网络上的真实 HTTP 调用连接；Worker 独立轮询 PostgreSQL 任务。
- Phase 3 没有调用 Groq、Google Chat 或 Embedding 模型，也没有注册 Support Agent Tool。所有订单安全与状态判断由普通代码和数据库约束完成。
- 业务调用会创建 LangSmith-compatible Trace ID，并关联到 delivery、receipt 和 worker task。GCP US endpoint、`resolveai-v2` 项目以及 Platform/Merchant/Worker 三类远端 Trace 已实际查询成功；当前 Key 不需要额外 Workspace ID。
