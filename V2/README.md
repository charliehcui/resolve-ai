# ResolveAI V2

ResolveAI V2 是一个用于演示 AI Application Engineering 的电商商家管理软件支持系统。Phase 10 / Task 15 已完成本地交付链路：Engineer Ticket 可以重新检查订单、发货和库存事实，并且只在确定性 Verification 通过后关闭。信息不足或业务目标仍未满足时，Ticket 保持打开。

Evaluation 只有 Quick Evaluation 和 Final Benchmark 两种模式，入口和历史结果见 `evals/README.md` 与 `evals/BENCHMARK_HISTORY.md`。

Support 的当前流程为诊断（Diagnosis）→ 动作计划（Action Plan）→ 审批（Approval）→ 执行（Execution）→ 验证（Verification）。诊断（Diagnosis）的最后一次模型响应同时给出结论和候选动作（Candidate Action），后续安全检查与写入全部由确定性代码（Deterministic Code）控制。订单、发货、库存刷新和已有任务重试支持用户确认后执行；授权过期要求用户重新授权，限流或外部故障等待恢复，缺失可信映射转人工。复现命令、文件职责和验证说明见 [Agent 与模拟器演示](docs/agent-simulator.md)。

## 当前真实数据流

```text
终端问题
  → 本地 token 身份映射
  → OpenRouter 查询规划 / 最多一次 Query Rewrite / Clarification 或 Handoff Intent
  → 公司、产品、版本、有效期过滤
  → OpenRouter 1024 维 Vector Search
  → 可选中文 BM25 + RRF + Qwen3 Reranking
  → OpenRouter 生成可核查 Claims
  → 普通代码校验引用存在性、公司范围、版本与有效期
  → OpenRouter 检查 Claim Support；删除无依据结论
  → 保存消息、检索记录、模型用量和 trace ID
  → 终端显示回答与来源
```

订单业务切片：

```text
Lab CLI 创建已付款订单
  → Platform HTTP API 校验账户公司并保存源订单
  → Platform 通过 HTTP 发送订单事件
  → Merchant 同一事务保存接收记录与 pending task
  → 独立 Worker 读取任务并检查店铺开关、渠道连接、付款和 SKU 映射
  → 条件满足：创建唯一商家订单并完成任务
  → 条件不满足：保存 blocked/failed 状态和真实错误码，不创建商家订单
  → Lab 分别查询平台、任务和商家订单事实
```

发货业务与恢复切片：

```text
Merchant order → dispatch task → Warehouse order
  → 测试操作者执行一次真实出库
  → Warehouse Shipment Event → Merchant receipt/task/shipment
  → Worker 推送 Platform → 三端独立查询
  → 异常时 Support Agent 使用只读 Shipment Tools 收集 Evidence
  → resend_shipment Action Plan → Policy Check → Scoped User Confirmation
  → Scope/Version Recheck → Merchant repair receipt/task
  → 补传既有 Shipment → Receipt Reconciliation → 三端 Verification
```

Customer Agent 不读取后台业务状态。Handoff 后同一 Conversation 的 active role 变为 Support；Support Agent 只获得只读业务 Tool。订单和发货写操作都必须经过普通代码执行的权限、批准、版本、幂等与 Verification 检查。Engineer Ticket 只在未解决、达到调查限制或用户明确请求时创建，不是 Agent 之间的通信工具。

渠道错误与库存切片：

```text
测试操作者设置单店连接状态
  → Order / Shipment / Stock Worker 读取该店真实状态
  → 保存 401/403/429/500/503/timeout、request ID 和失败记录
  → 其他店铺和公司继续独立处理

测试操作者写 Warehouse stock
  → Merchant 按 max(physical - reserved - safety, 0) 创建发布任务
  → Worker 通过 Platform HTTP contract 发布版本化库存
  → Support 的 GetStockFacts 依次读取映射，再读取 Warehouse 与 Platform
  → 普通代码按版本和时间窗口输出 consistent / waiting / difference / insufficient_information
```

## 环境要求

- Python 3.11+
- Docker Desktop
- 从 `.env.example` 创建且被 Git 忽略的 `V2/.env`
- 仅在主动运行模型问答时需要可用的 OpenRouter / OpenRouter 配置

模型名称和 Key 只从 `.env` 读取。`LANGSMITH_TRACING` 默认必须保持 `false`；只有以后明确批准远端追踪时才开启。不要把 `.env`、`.local/test_tokens.json`、`.local/service_tokens.json` 或任何 Secret 提交到 Git。

主要环境变量：

| 变量 | 用途 | 本地固定测试 |
| --- | --- | --- |
| `POSTGRES_*` / `DATABASE_URL` | PostgreSQL 与 pgvector | 必需 |
| `OPENROUTER_API_KEY` / `OPENROUTER_MODEL` | Customer 问答、查询规划与引用检查 | 使用安全占位值，不调用 |
| `OPENROUTER_API_KEY` / `OPENROUTER_MODEL` | Support 复杂调查 | 使用安全占位值，不调用 |
| `EMBEDDING_MODEL` / `EMBEDDING_DIMENSION` | Customer 文档向量；维度固定 1024 | 使用安全占位值，不导入文档 |
| `RERANK_MODEL` / `RETRIEVAL_MODE` | 本地重排与检索模式 | 按 `.env.example` |
| `LANGSMITH_TRACING` | 远端 Trace 上传 | 必须为 `false` |
| `SUPPORT_MAX_*` | Support 工具、时间和错误预算 | 可选，使用代码默认值 |

## 安装与启动

```powershell
cd E:\AI_Engineer\Langchain\resolve-ai\V2
Copy-Item .env.example .env
# 修改数据库密码；本地固定测试保留模型占位值和 LANGSMITH_TRACING=false
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.lock
docker compose up -d --build
docker compose ps
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
```

Compose 的一次性 `init` 服务会从空数据库执行全部 migration，再运行 `simulator.lab.bootstrap`。它在被忽略的 `.local/test_tokens.json` 和 `.local/service_tokens.json` 生成本机 token。业务服务和 API 只读挂载这些 token；API 镜像不包含 `simulator/lab/` 或 `evals/`。

若只在宿主机运行 CLI，也可以单独启动数据库后执行：

```powershell
docker compose up -d db
.\.venv\Scripts\python -m backend.app.cli db init
.\.venv\Scripts\python -m simulator.lab.bootstrap
```

`backend.app.cli doctor`、`docs import` 和真实聊天会调用外部模型或 Embedding，不属于默认本地启动或固定回归。只有获得明确授权后才运行：

```powershell
.\.venv\Scripts\python -m backend.app.cli doctor
.\.venv\Scripts\python -m backend.app.cli docs import
```

第一次运行 `hybrid_rerank` 会从 Hugging Face 下载 `RERANK_MODEL` 指定的 Qwen3 Reranker 权重；权重保存在用户缓存中，不进入仓库。

## 终端问答

PowerShell 示例：

```powershell
$tokens = Get-Content .local\test_tokens.json | ConvertFrom-Json
$env:RESOLVEAI_TOKEN = $tokens.'admin-a'
.\.venv\Scripts\python -m backend.app.cli chat "如何开启订单同步？"
```

选择检索模式：

```powershell
.\.venv\Scripts\python -m backend.app.cli chat "ORDER_SYNC_DISABLED 是什么意思？" --mode vector_only
.\.venv\Scripts\python -m backend.app.cli chat "ORDER_SYNC_DISABLED 是什么意思？" --mode hybrid
.\.venv\Scripts\python -m backend.app.cli chat "ORDER_SYNC_DISABLED 是什么意思？" --mode hybrid_rerank
```

当前默认是 `vector_only`。4 个 Phase 2 开发案例中三种模式均为 4/4 Top-5 命中；`vector_only` 平均延迟最低且预期资料排名最好。三种模式仍保留用于后续评估，不把小样本结论扩大为普遍结论。

继续同一会话：

```powershell
.\.venv\Scripts\python -m backend.app.cli chat "这个开关会补回全部历史订单吗？" --conversation <conversation-id>
```

## 订单业务流

正常同步：

```powershell
.\.venv\Scripts\python -m simulator.lab.cli shop sync --shop shop-a --enabled true
.\.venv\Scripts\python -m simulator.lab.cli order create --shop shop-a --order O-1001 --sku SKU-1 --qty 2 --amount-minor 20000
```

产生真实受阻任务：

```powershell
.\.venv\Scripts\python -m simulator.lab.cli shop sync --shop shop-a --enabled false
.\.venv\Scripts\python -m simulator.lab.cli order create --shop shop-a --order O-1002 --sku SKU-1 --qty 1 --amount-minor 5000
```

第二条命令仍会留下平台订单、商家接收记录和 `ORDER_SYNC_DISABLED` 任务，但不会生成商家订单。重新发送完全相同的订单会复用稳定事件编号；内容变化则返回冲突。订单恢复必须从 Support case 创建受限方案，不能由查询接口偷偷补数据。

## 订单恢复

先由 Support case 创建一笔受限方案，再由同公司管理员批准：

```powershell
.\.venv\Scripts\python -m backend.app.cli action propose <case-id>
.\.venv\Scripts\python -m backend.app.cli action decide <action-id> --decision approve
.\.venv\Scripts\python -m backend.app.cli action show <action-id>
```

如果方案需要同时开启店铺订单同步，必须在提案时显式增加 `--enable-order-sync`。批准有效期为十分钟。批准后程序重新检查公司、店铺、源订单版本和店铺版本，再通过 Merchant HTTP repair contract 创建幂等任务。Worker 完成后，独立 Verification 重新查询 Platform 与 Merchant，并核对事件、SKU、数量、金额、任务状态和唯一订单数。重复批准不会新增 receipt、decision 或业务订单。

## 发货业务流与恢复

创建并查询真实发货：

```powershell
.\.venv\Scripts\python -m simulator.lab.cli order dispatch --shop shop-a --order O-1001
.\.venv\Scripts\python -m simulator.lab.cli shipment create --shop shop-a --order O-1001 --carrier test-express --tracking TEST-1001
.\.venv\Scripts\python -m simulator.lab.cli shipment show --shop shop-a --order O-1001
```

关闭发货同步后，Warehouse 和 Merchant 仍保存真实 Shipment，Platform 保持未发货，Merchant task 保存 `SHIPMENT_SYNC_DISABLED`：

```powershell
.\.venv\Scripts\python -m simulator.lab.cli shop shipment-sync --shop shop-a --enabled false
```

从已有 Support case 创建发货恢复方案：

```powershell
.\.venv\Scripts\python -m backend.app.cli action propose <case-id> --type recover_shipment --enable-shipment-sync
.\.venv\Scripts\python -m backend.app.cli action decide <action-id> --decision approve
.\.venv\Scripts\python -m backend.app.cli action show <action-id>
```

恢复只补传 Merchant 已保存的 Shipment，不提供第二次 Warehouse 出库能力。成功必须由 Warehouse、Merchant、Platform 三端读回共同证明；15 秒内无法证明时保持 `pending`。

准备 response-lost 演示场景：

```powershell
.\.venv\Scripts\python -m simulator.lab.cli seed --scenario shipment_response_lost
```

该场景先关闭发货同步并完成一次真实出库，再让下一次批准补传在 Platform transaction 已提交后延迟响应。Worker 将本地结果保存为 `unknown`，随后读取 Platform 事实并对账；不会重新出库。Phase 6 实际验收中，重复恢复后 Warehouse、Merchant、Platform 均保持一条 Shipment。

## 渠道故障与连接恢复

连接状态只能由测试操作者设置或恢复。Support Agent 和普通商家没有连接写工具或业务接口：

```powershell
.\.venv\Scripts\python -m simulator.lab.cli connection set --shop shop-a --status auth_expired
.\.venv\Scripts\python -m simulator.lab.cli connection restore --shop shop-a
```

可控状态包括 `auth_expired`、`forbidden`、`rate_limited`、`internal_error`、`unavailable` 和 `timeout`。每个状态只作用于指定公司和店铺；Worker 不进行无限重试，也不会让模型自行恢复授权。

## 库存发布与只读调查

以下命令把 Warehouse 的实物数设为 80、占用数设为 10；种子规则的安全保留为 5，因此正常发布结果为 65：

```powershell
.\.venv\Scripts\python -m simulator.lab.cli stock publish --shop shop-a --sku SKU-1 --warehouse-sku MERCHANT-SKU-1 --physical 80 --reserved 10
```

库存写入只存在于测试操作者的 Warehouse 场景入口和正常商家发布流程。Support Agent 只有 `GetStockFacts`：先读取商品关系，再读取 Warehouse 和 Platform，按来源版本与 30 秒传播窗口解释结果。映射、版本或时间缺失时返回信息不足，不强行计算；本期不提供库存修复。

## Engineer Ticket 复查、API 与 React 演示

初始化后启动 API：

```powershell
docker compose up -d --build api
```

OpenAPI 位于 `http://127.0.0.1:8000/docs`。CLI、API 和前端使用同一套会话、Action 和 Ticket 业务函数。Ticket CLI 示例：

```powershell
.\.venv\Scripts\python -m backend.app.cli ticket create <conversation-id> --reason "需要人工支持"
.\.venv\Scripts\python -m backend.app.cli ticket list --token <engineer-token>
.\.venv\Scripts\python -m backend.app.cli ticket show <ticket-id> --token <engineer-token>
.\.venv\Scripts\python -m backend.app.cli ticket recheck <ticket-id> --token <engineer-token>
.\.venv\Scripts\python -m backend.app.cli ticket export <ticket-id> --token <engineer-token>
```

`ticket recheck` 只允许显式获得 Ticket read grant 的工程师调用：

- `RESOLVED`：订单、发货或库存的确定性 Verification 通过，Ticket 关闭。
- `UNRESOLVED`：标识符完整，但实际业务结果仍不满足，Ticket 保持或重新变为打开。
- `NEEDS_INFO`：缺少 case、shop、order、SKU 或明确业务目标，Ticket 保持打开。

订单复查比较 Platform 与 Merchant 的事件、SKU、数量、金额、唯一订单和任务状态。发货复查比较 Warehouse、Merchant 与 Platform 三端的一次出库事实。库存复查继续使用 `max(physical - reserved - safety, 0)`、来源版本和时间窗口。模型文字不能关闭 Ticket。

API 新增：

- `POST /api/v1/tickets/{ticket_id}/recheck`
- `GET /api/v1/tickets/{ticket_id}/export`

启动最小 React 页面：

```powershell
cd frontend
npm install
npm run dev
```

页面使用 merchant token 展示会话、当前 Agent、Citation、Evidence、Action Plan、Approval、Verification 和 Ticket；Engineer token 只能加载显式分配给该工程师的 Ticket，并可运行确定性复查。关闭后的 Ticket 仍会显示在会话和工程师队列中。没有 customer selector，也不能手工切换 Agent。

最小 Playwright E2E 使用直接人工请求，不调用 OpenRouter 或 OpenRouter：浏览器创建商家会话和 Ticket，授权 Engineer 读取并复查；由于没有业务标识符，结果必须为 `NEEDS_INFO` 且保持打开。

## Evaluation

```powershell
.\.venv\Scripts\python -m evals.run --validate
.\.venv\Scripts\python -m evals.run --category workflow
# Only run this when a Final Benchmark has been explicitly requested:
.\.venv\Scripts\python -m evals.run --mode final --changes "Describe the measured change"
```

Quick Evaluation 默认只选指定类别的 4–5 个现有 Cases，不运行其他模块。Final Benchmark 显式选择后才运行当前完整 44 Cases / 64 Runs。两种模式遇到 Error / Timeout 都立即停止；Quick 复用专用评估数据库和文档，Final 使用新的隔离环境。完整说明见 `evals/README.md`，历史数据及可信度见 `evals/BENCHMARK_HISTORY.md`。报告只有最新结果与重要历史记录，失败案例保留原始证据，凭证和累计费用账本保存在被忽略的 `.local/eval/`。

## 测试

测试使用真实 PostgreSQL + pgvector；普通 unit、contract、graph 和 regression tests 使用确定性 mock/stub，并强制关闭 LangSmith tracing，不消耗外部模型调用：

```powershell
.\.venv\Scripts\python -m pytest
```

`tests/conftest.py` 会在导入应用模块前覆盖 OpenRouter 与 Embedding 配置为测试占位值；CI 同样使用占位值。`tests/test_llm_fallback.py` 使用合成客户端验证最多一次回退、错误分类与连续解析失败；这些测试不计入真实模型评估。

Phase 10 历史验证结果见 `docs/phase-10-report.md`。普通回归测试显式关闭 LangSmith，并使用模型占位 Key。Evaluation Phase 1.5 的真实模型验证与基线报告单独保留。

## 当前限制

- 当前资料集只有 16 个片段，检索质量对照只是小型开发验证，不代表大规模性能。
- 本地 Qwen3 Reranker 真实可运行，但 CPU 延迟明显高于 Vector/Hybrid；默认模式会随后续真实数据重新评估。
- Citation Validation 会降低无依据回答风险，但语义模型仍可能误判，不宣称保证答案正确。
- Customer Agent 不读取任何真实订单、店铺、平台、仓库、日志或库存后台状态。
- 订单/发货模拟器只覆盖一个包裹的完整发货及必要拒绝状态；不连接真实平台，不查询真实物流，不做拆单或促销分摊。
- Phase 3–7 的业务安全、批准、执行、对账、验证和库存比较由普通代码决定；LLM 不负责最终写入许可或成功判定。
- Phase 3 的 Platform、Merchant 和 Worker Trace 已在 LangSmith `resolveai-v2` 项目中远端验证。
- Phase 4、6、7、8 延期的真实模型调查和远端 Trace 当前不补跑；本地 Evidence、确定性安全检查和业务结果是本轮交付依据。
- 当前实现目标是 Effectively-once Business Effect，不承诺严格 Exactly-once Execution。稳定 request ID、唯一约束、execution claim/lease、receipt/reconciliation 和 read-after-write verification 共同防止重复业务效果。
- Ticket recheck 和关闭已完成；本期不实现工单认领、重新分配或值班系统。无匹配分配规则的 Ticket 保持未分配。
- Compose 每次重新执行 `init` 都会刷新本地演示用户 token；始终以最新 `.local/test_tokens.json` 为准。
- API 镜像包含本地 Qwen3 Reranker 所需的 Torch/Transformers，首次 Linux 构建体积较大；一次性 init 和业务服务镜像不包含这些 AI 依赖。
- 产品规则属于本项目测试产品，不代表真实电商平台。
- Docker 本地服务可以独立启动；模型地域和账户额度只影响以后单独授权的真实模型验收。

架构边界、三条演示路径和最终交付检查分别见 `docs/architecture.md`、`docs/demo.md` 与 `docs/phase-10-report.md`。


Evaluation Phase 1.5: all chat/structured/tool/judge calls use OpenRouter. Google SDK remains ONLY for unchanged embeddings, configured with EMBEDDING_API_KEY/EMBEDDING_MODEL. See evals/README.md and evals/BENCHMARK_HISTORY.md for persistent budget and benchmark limitations.
