# ResolveAI V2

ResolveAI V2 是一个面向电商商家管理软件的生产级 AI 技术支持系统。系统把客户自助、AI 调查、受控恢复、结果验证和人工工程师接手连接成一条完整流程。

项目使用 React 前端、FastAPI 后端、LangGraph 持久化流程、PostgreSQL + pgvector，以及相互隔离的业务测试服务。所有组件通过 Docker Compose 运行，开发、测试和演示使用同一套环境。

## 核心目标

- 根据产品资料回答问题，并提供可核对的引用。
- 使用真实服务数据调查订单、发货和库存问题。
- 区分已确认事实、可能原因、冲突证据和未知信息。
- 所有业务写操作必须先经过人工批准。
- 根据最终业务状态验证结果，不信任模型结论或单次 HTTP 成功响应。
- 在超时、重复请求、工作进程重启和响应丢失后安全恢复。
- 把未解决问题和完整证据交给人工工程师。
- 整个系统通过 Docker 运行，不依赖宿主机安装 Python、Node.js 或 PostgreSQL。

## 支持架构

```text
Level 1 — Customer Self-Service
Customer Agent
├── Agentic RAG
│   ├── Query Routing
│   ├── Hybrid Search
│   ├── Reranking
│   └── Citation Validation
└── Conversation
        ↓
     Handoff

Level 2 — AI Investigation
Support Agent
├── Dynamic Tool Selection
├── Evidence Collection
├── Evidence Validation
├── Diagnosis
└── Action Proposal
        ↓
   ┌────┴────┐
Resolution   Action Needed
                  ↓
           Human Approval
                  ↓
            Execute + Verify
                  ↓
            Still Unresolved
                  ↓
Level 3 — Human Engineer
```

### Level 1 — Customer Self-Service

客户智能体（Customer Agent）负责产品问答、必要追问、文档检索、引用和会话状态。它只能访问客户可见资料，不能读取内部业务记录，也不能请求写操作。

代理式检索增强生成（Agentic RAG）包含：

- 查询路由（Query Routing）：选择正确的资料范围，或把实际故障送入调查流程。
- 混合检索（Hybrid Search）：结合向量检索和 PostgreSQL 全文检索。
- 重排序（Reranking）：在生成回答前重新排列候选片段。
- 引用验证（Citation Validation）：检查来源是否存在、是否对当前用户可见、是否匹配产品版本，以及能否支持对应结论。

当问题需要业务数据时，交接（Handoff）会把问题摘要、已知编号、已完成检查和缺失信息传给支持智能体。

### Level 2 — AI Investigation

支持智能体（Support Agent）使用一组固定且受公司范围限制的工具调查具体问题：

- 动态工具选择（Dynamic Tool Selection）：选择下一项必要的只读查询。
- 证据收集（Evidence Collection）：读取平台、管理软件和仓库的真实记录。
- 证据验证（Evidence Validation）：检查来源、权限、时间、版本和对象关系。
- 诊断（Diagnosis）：把已确认事实、可能原因、相反证据和未知信息分开。
- 动作提案（Action Proposal）：证据充分时创建有限恢复方案。

支持智能体不能直接执行恢复。普通程序负责检查方案、记录批准、执行允许步骤和验证结果。

### Level 3 — Human Engineer

系统无法确认安全解决方案时，会创建包含调查时间线、已确认事实、失败请求、已有动作、证据、未知信息和下一项检查的工单。被分配的工程师可以查看案件，并在外部处理完成后使用相同验证规则重新检查。

## 主要业务流程

### 订单未进入管理软件

平台保存有效订单并向管理软件发送事件。管理软件保存接收记录并创建后台任务。受控故障可以让订单停留在处理流程中，而没有进入管理软件订单表。

支持智能体检查源订单、店铺状态、商品对应关系、接收事件和后台任务。满足条件时提出 `recover_order`。管理员批准后，执行器只处理指定订单，并检查两端的商品、数量、金额、版本和唯一性。

### 发货状态未更新平台

管理软件把订单发送到仓库。仓库必须先收到订单，才能创建发货事实。发货事件返回管理软件，再由管理软件更新平台。

支持智能体检查仓库发货事实、管理软件记录、发送尝试、请求回执和平台状态。满足条件时提出 `recover_shipment`。执行器只补传已有发货事实，不会产生第二次仓库出库。

### 库存差异

库存调查只读。系统比较仓库实物数、占用数、安全保留数、管理软件规则、来源版本和平台数量，用于区分合理差异、正常处理延迟和未解决的更新失败。系统不提供库存写入工具。

## 生产级系统设计

```text
Browser
   ├── Frontend — React + TypeScript + Vite
   └── Backend API — FastAPI + LangChain + LangGraph
                         ├── Customer Agent
                         ├── Support Agent
                         ├── Approval and Action Service
                         ├── Verification Service
                         └── Ticket Service
                                  ↓
                         PostgreSQL + pgvector

Backend and Worker
        ↓ authenticated HTTP
Merchant Service ↔ Platform Service
        ↕
Warehouse Service
```

Docker Compose 运行以下服务：

| 容器 | 职责 |
| --- | --- |
| `frontend` | 构建并提供 React 页面 |
| `backend` | 提供 Web API、智能体、批准、验证和工单功能 |
| `worker` | 处理持久化后台任务 |
| `merchant` | 保存管理软件订单、设置、事件、任务和发货记录 |
| `platform` | 保存平台订单、发货状态、库存状态和请求回执 |
| `warehouse` | 保存仓库订单、发货事实、库存事实和请求回执 |
| `db` | 运行 PostgreSQL + pgvector，并使用持久化数据卷 |
| `init` | 执行数据库迁移并创建本地账户 |

前端和后端分别暴露本地端口。前端通过 `VITE_API_BASE_URL` 调用后端 API，后端只允许配置中的前端来源。数据库和三个业务服务只位于 Docker 内部网络，不向浏览器开放。

所有应用服务使用健康检查、明确的环境变量、独立数据库账户、结构化错误、请求编号和持久化数据卷。密钥不写入镜像、前端代码、日志或 Git。

## 技术栈

| 层 | 技术 |
| --- | --- |
| 前端 | React、TypeScript、Vite、CSS |
| 后端 | Python、FastAPI、Uvicorn、Pydantic |
| AI 流程 | LangChain、LangGraph |
| 聊天模型 | 通过 `GROQ_MODEL` 配置的 ChatGroq |
| 数据 | PostgreSQL、pgvector、psycopg |
| 检索 | 向量检索、PostgreSQL 全文检索、可配置重排序 |
| 服务调用 | HTTPX |
| 测试 | pytest、Ruff、Vitest、ESLint、TypeScript |
| 运行环境 | Docker、Docker Compose |

## 项目目录

```text
V2/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── db.py
│   │   ├── auth.py
│   │   ├── model.py
│   │   ├── graph.py
│   │   ├── customer_agent.py
│   │   ├── support_agent.py
│   │   ├── search_docs.py
│   │   ├── citations.py
│   │   ├── tools.py
│   │   ├── evidence.py
│   │   ├── actions.py
│   │   ├── verify.py
│   │   ├── tickets.py
│   │   ├── runs.py
│   │   └── reports.py
│   ├── tests/
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── src/
│   ├── Dockerfile
│   └── package.json
├── services/
│   ├── common.py
│   ├── merchant.py
│   ├── platform.py
│   ├── warehouse.py
│   └── worker.py
├── lab/
│   ├── setup.py
│   ├── scenarios.py
│   └── checks.py
├── db/
├── docs/product/
├── evals/
├── compose.yaml
├── .env.example
├── .gitignore
└── README.md
```

文件只在对应功能开始实现时创建，避免没有行为的空层和未使用的抽象。

## Docker 运行

根据 `.env.example` 创建 `.env` 并填写本地密钥，然后启动完整系统：

```powershell
docker compose up --build
```

打开 `http://localhost:3000` 使用前端。后端 API 运行在 `http://localhost:8000`，PostgreSQL 数据保存在命名数据卷中。

在相同容器环境运行全部自动检查：

```powershell
docker compose run --rm tests
```

宿主机不需要安装 Python、Node.js、PostgreSQL 或 pgvector。

## 安全与可靠性规则

- 服务器端令牌绑定账户、公司、角色和允许店铺。
- 浏览器不能提供可信公司编号或服务地址。
- 客户智能体不能访问调查工具。
- 支持智能体只能使用固定只读工具和动作提案工具。
- 模型不能执行 SQL、系统命令、文件操作或任意 HTTP 请求。
- 恢复动作只允许 `recover_order` 和 `recover_shipment`。
- 批准绑定动作、公司、目标、参数、证据、版本和方案摘要。
- 每个写步骤使用稳定的幂等键（Idempotency Key）。
- 超时在目标状态或回执完成检查前属于未知结果。
- 确定性代码负责判断业务目标是否完成。
- 跨公司访问、未批准写入、重复订单、重复出库和假成功都会阻止发布。

## 完成标准

- Level 1 可以提供有来源的回答，并把实际故障交给 Level 2。
- Level 2 通过真实服务调用调查并保存经过验证的证据。
- 订单和发货恢复都完成提案、批准、执行和验证。
- 超时、响应丢失、重复请求和工作进程重启不会产生重复业务结果。
- Level 3 收到完整工单，并使用相同规则重新验证。
- 前端提供客户聊天、案件证据、动作批准、执行状态和工程师工单页面。
- Docker 环境能够一致地启动完整系统并运行测试。
- 评测报告保留失败、版本、调用量、延迟和测试边界。

## 项目边界

ResolveAI V2 使用隔离的本地服务产生接近真实业务的数据和故障行为。项目不连接真实电商平台，不处理真实客户数据，不执行退款、支付、财务核算、真实仓库出库、库存写入或模型训练。在完成外部安全审查和负载验证前，不声称已经部署到生产环境。
