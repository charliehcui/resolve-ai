# ResolveAI V1 → V2 渐进式重构计划

> 状态：Phase 10 / Task 15 已完成本地实现与交付验证。Phase 9 只保留评估基础，付费 Benchmark、176 次对照、Holdout 30×3 和批量外部验收按当前决定不执行。
> 唯一事实来源（Source of Truth）：`V2/building-plan-V2.md`。  
> 本文件只是实施计划。业务背景、Agent 职责、RAG、Tools、数据模型、API、前端、业务流程、测试和评估如与事实来源冲突，一律以 `building-plan-V2.md` 为准。  
> 本轮明确的 Groq / Google 模型路由（Model Routing）、Phase 审批门槛和 V1 只读边界作为执行约束；它们不改变 V2 的业务范围。

## 1. 结论摘要

V1 已经具备一个可运行的双 Agent 支持系统底座：FastAPI、LangGraph、PostgreSQL/pgvector、checkpoint、RAG、工具调用（Tool Calling）、Evidence、Approval、幂等写入、Verification、Ticket、React 前端、Docker 和较完整的测试框架都已经存在。

但是，V1 的业务故事、Agent 边界和主要数据流与 V2 明显不同：

- V1 是通用 B2B SaaS 的通知/报表导出支持；V2 是电商商家管理系统的订单、发货和库存支持。
- V1 的 Customer Agent 会读取部分客户实时状态；V2 的 Customer Agent 只能做自助 RAG，不能读取订单、店铺、平台或后台运行事实。
- V1 用 Ticket 把 Customer Agent 交给 Support Agent；V2 用结构化 Handoff 直接切换会话角色，Ticket 只用于真正的人工升级（Escalation）。
- V1 的 Support Agent 使用固定、并行的工具集合并依赖 Internal RAG；V2 要根据当前 Evidence 动态选工具、受预算约束、可提前停止，而且本期没有 Internal RAG。
- V1 的模拟器主要返回预置状态；V2 要由平台、商家系统、仓库和后台任务之间的真实 HTTP/数据库状态变化产生故障与恢复结果。
- V1 只有一个通用恢复动作；V2 需要订单恢复、发货恢复、响应丢失后的对账、执行租约、崩溃恢复和严格的业务结果验证。

因此，本次工作不是在 V1 上换文案，而是在 `V2/` 中独立实现新的业务域和主数据流。`V1/` 永久作为历史版本保留，只能用于只读参考和复制质量合适的实现思路。不得删除、移动、重命名或修改 V1 的代码、测试、migration、README 和其他文件；V2 运行时也不得 import、读取或依赖 V1 目录。

### 1.1 实施取舍

这是 AI Application Engineer 求职和作品集项目。实现优先级是：清晰、真实、可运行、可解释、便于演示。除非 `building-plan-V2.md` 明确要求或当前纵向功能确实需要，否则不加入 Message Queue、Kafka、Redis、Kubernetes、Event Bus、CQRS、Event Sourcing、多数据库、多向量数据库、复杂权限框架或多层 Repository/Service/Manager/Factory 抽象。

平台、商家、仓库和 Support 仍按事实来源保持代码与数据所有权边界，但只使用小型 FastAPI 测试服务、普通 Python 模块/函数和一套 PostgreSQL + pgvector。新增组件必须能说明它正在解决哪个当前需求，否则不加入。

## 2. V1 当前状态

### 2.1 已实现的主链路

当前链路大致为：

```text
React Customer View
  → FastAPI support session
  → Customer LangGraph
  → Customer RAG / customer read tools
  → SupportHandoff
  → 创建 Ticket
  → Support LangGraph + 固定 read tools + Internal RAG
  → Evidence 校验
  → ActionProposal → Approval interrupt
  → 执行 retry_failed_operation
  → read-after-write Verification
  → 解决或工程师升级
```

业务模拟集中在 ResolveLab，覆盖通知端点、通知开关、报表导出依赖超时与状态冲突。数据库保存会话、Ticket、Action Proposal、Approval 和 Execution；向量库同时保存 CUSTOMER 与 INTERNAL 文档。

### 2.2 工程成熟度

- 后端、模拟器和前端可以通过 Docker Compose 组织运行。
- LangGraph 使用 PostgreSQL checkpoint，写动作已有 Approval、过期检查、幂等键和结果验证的基础模式。
- 测试覆盖会话、RAG 隔离、工具、Evidence、Approval、恢复、HITL 与评估框架。
- 现有评估框架可以连接 LangSmith，但追踪不是所有 Agent、LLM、RAG、rerank 和 tool 调用的强制主路径。
- 已保存的 V1 评估结果并未达到稳定验收状态；现有报告中大量案例因模型提供方或等待/验证路径失败。因此 V1 只能作为工程基线，不能作为 V2 质量基线。

## 3. 差异分类

### 3.1 可以参考并复用到 V2 的部分

| V1 能力 | 处理方式 | 保留原因 |
| --- | --- | --- |
| FastAPI 应用、Pydantic schema、统一异常处理模式 | 迁移工程骨架，替换业务路由与 schema | 技术底座与 V2 兼容 |
| PostgreSQL、SQLAlchemy 连接与事务模式 | 保留连接层思想，重建 V2 表和权限 | V2 仍要求单 PostgreSQL 实例与真实事务 |
| pgvector、文档切分和 ingestion 基础模式 | 保留机制，替换语料、表结构、embedding 和检索管线 | V2 仍有 Customer RAG |
| LangGraph、PostgresSaver、thread/checkpoint 模式 | 保留机制，重写 State 与节点 | V2 需要可暂停、可恢复的多阶段流程 |
| Evidence 数据结构中的来源、时间、版本、所有权思想 | 扩展为 V2 统一 Evidence | 与 V2 的证据驱动调查一致 |
| Approval、过期、作用域、版本复核、幂等写和 read-after-write | 保留安全模式，改写为订单/发货动作 | V2 明确要求同类控制 |
| HTTP 客户端超时、错误映射和 request ID 模式 | 迁移为平台/商家/仓库 client | 真实跨服务数据流需要这些能力 |
| pytest、SQLite/测试数据库 fixture、mock 边界、契约测试模式 | 保留测试工程方法 | 可降低分阶段迁移风险 |
| React/Vite/TypeScript/Tailwind 工程壳与 fetch 封装思路 | 保留构建配置和通用 UI 基础 | V2 仍需要前后端交互，但页面和合同会改变 |
| Dockerfile、Compose、CI、lint 的组织方式 | 作为模板迁移并重新接线 | V2 需要干净环境可复现交付 |
| Eval runner 的案例驱动、重复运行和报告聚合思想 | 重写指标与数据集后保留 | V2 要做 176 次对照运行与可审计报告 |
| 对检索内容和工具返回值按不可信输入处理 | 保留并加强 | V2 的安全边界不变 |

“复用”只表示在 `V2/` 中重新使用合适的设计或复制必要代码。不得修改 V1，也不得让 V2 运行时依赖 V1。所有包含通知、报表导出、旧 customer_id 或旧 Ticket 语义的实现都不进入 V2。

### 3.2 必须修改的部分

| 范围 | V1 | V2 修改目标 |
| --- | --- | --- |
| 背景故事 | 通知与报表导出支持 | 电商商家管理：订单未同步、发货状态未回传、库存解释 |
| 身份模型 | 少量固定 customer ID | 公司、用户、角色、店铺、渠道、作用域 token；所有入口强制租户隔离 |
| Customer Agent | RAG 加客户状态读取 | 只做澄清、查询改写、Customer RAG、引用回答或 Handoff |
| Support Agent | 固定工具组合，先并行查询 | 根据 Evidence 动态选择下一工具；有预算、提前停止、补问标识符和安全升级 |
| Agent 交接 | 先建 Ticket，再运行 Support | Handoff 直接改变 conversation.active_role；后续消息继续给 Support |
| Ticket | Agent 间运输对象，也保存结果 | 只在未解决、达到预算、明确要人工时创建；归属具体工程师或待分配队列 |
| RAG | 纯向量 top-k，CUSTOMER/INTERNAL 共表 | Customer-only；向量 + 中文关键词/BM25 + RRF + rerank + 引用程序校验 |
| 工具 | 账户/通知/平台/后台任务 | 店铺、订单、处理记录、连接、发货、库存等 V2 只读工具与受控恢复动作 |
| 并行工具 | 固定工具预先并行 | 只有相互独立且已具备输入的只读工具可以并行；依赖结果的调用必须顺序执行 |
| 动作 | retry_failed_operation | recover_order、recover_shipment；各自独立 proposal、approval、execution、verify |
| Evidence | 针对旧 SaaS 场景 | 统一来源系统、对象、公司/店铺、时间、版本、request/trace ID、矛盾与可信度 |
| Verification | 单一动作后的读回 | 订单唯一性/商品/数量/金额；发货三系统一致性；库存只读公式；Ticket recheck |
| 模拟器 | 单服务、四个预设客户场景 | 平台、商家、仓库、worker 分离；故障从真实调用和持久状态演化产生 |
| 数据库 | support session/ticket/action 为中心 | 身份、会话、run、检索、业务事实、事件、任务、动作步骤、工单全链路模型 |
| API | `/support/*` 与旧 approval 接口 | versioned conversation/case/action/ticket API；按角色和资源作用域授权 |
| 前端 | Customer/Support demo toggle | 商家会话、Support 调查时间线、审批、工程师工单分别按身份展示 |
| 模型 | 仅 ChatGroq 工厂 | Groq/Google 路由、结构化输出、Google tool calling、运行元数据与降级规则 |
| 观测 | 部分 LangSmith/eval 接入 | Agent、LLM、RAG、rerank、tool、write、verify 全链路真实 trace |
| 文档 | V1 README/计划/截图 | V2 故事、架构、命令、数据流、安全边界、评估和已知限制 |

### 3.3 V2 不采用、不复制的 V1 部分

以下内容继续留在 V1 历史版本中，但不得复制为 V2 的兼容层：

- 通知端点、通知开关、报表导出、旧 background operation 的领域模型、工具、提示词、知识文档、测试和演示数据。
- ResolveLab 单体模拟器及其四个预设客户分支；由 V2 的 platform/merchant/warehouse/worker 服务替代。
- `search_internal_knowledge`、INTERNAL 文档 ingestion、内部文档提示词和 Internal RAG 结论路径。
- Customer Agent 读取 current product context、recent activity 或任何业务后台事实的能力。
- Customer → Ticket → Support 的交接路径，以及把 Ticket 当 Agent transport 的字段和 API。
- Support Agent “每次先调用全部主要工具”的固定调查策略。
- 通用 `retry_failed_operation` 动作以及只适用于旧导出流程的 proposal/policy/verification。
- 旧分类标签、旧 scenario/customer ID 白名单和前端 URL customer selector。
- 旧 API DTO、旧前端 Support/Customer demo toggle，以及建立在旧 Ticket 语义上的组件。
- V1 旧知识库、旧 eval cases、旧报告与旧截图。
- V1 migration、Docker service、环境变量和其他只适用于旧架构的代码。

### 3.4 V2 新增能力

1. 公司/用户/角色/店铺/渠道级身份和授权，贯穿会话、工具、写入、Ticket 与工程师访问。
2. 中文 Customer RAG：查询路由、最多一次改写、hybrid search、RRF、语义 rerank、引用存在性/权限/版本/claim support 校验。
3. 结构化 Handoff 和会话角色粘滞（role stickiness）：Handoff 后所有新消息进入 Support Agent。
4. Evidence 驱动的动态调查器：工具预算、时间预算、错误预算、提前停止、缺少标识符时补问。
5. Groq/Google 模型路由与 Google 复杂工具调用；独立工具安全并行、有依赖工具顺序调用。
6. 真实订单数据流：平台订单 → HTTP 事件 → 商家接收记录 → worker → 商家订单。
7. 真实发货数据流：商家发货指令 → 仓库订单/出库 → 事件 → 平台状态更新。
8. 订单和发货恢复：Proposal → Approval → 版本复核 → Execution → 独立 Verification。
9. 响应丢失、进程崩溃、重复事件、并发 worker、执行 lease/claim、receipt/reconciliation。
10. 多店铺/多渠道错误：401、403、429、500、503、timeout；连接恢复只属于测试操作员。
11. 库存只读调查和 `max(physical - reserved - safety, 0)` 解释，不提供库存写工具。
12. 仅为人工接手创建的 Engineer Ticket、真实分配规则、最小权限读取和 recheck。
13. 50 个开发案例、30 个冻结保留案例、三类对照实验和 176 次比较运行。
14. 每个关键步骤的 LangSmith trace、运行成本/用量/延迟与 HTML 报告。
15. CLI-first 的完整流程；后续 API 和 React UI 只使用同一组普通业务函数，不增加多层前端/后端架构。

## 4. 目标架构与数据流

### 4.1 服务边界

```text
CLI / later React UI
      │
      ▼
Support App / small FastAPI API
      ├── Auth + Conversation + Agent Runs
      ├── Customer Agent ── Customer RAG
      ├── Structured Handoff
      ├── Support Agent ── Dynamic Read Tools
      ├── Action / Approval / Execution / Verification
      └── Engineer Ticket / Report / Eval
                │
                ├── Platform Service
                ├── Merchant Service + Worker
                └── Warehouse Service + Worker

One PostgreSQL instance
  ├── support-owned data
  ├── platform-owned data
  ├── merchant-owned data
  ├── warehouse-owned data
  └── pgvector customer knowledge
```

本地部署共用一个 PostgreSQL 实例，不增加第二套数据库或向量数据库。平台、商家、仓库和 Support 各自的普通 Python 数据访问函数只能操作本模块拥有的表。Support Agent 必须通过 Tool / HTTP Contract 读取业务事实，不能为了方便直接跨域查表。这里的多个 FastAPI 程序只是用于真实模拟系统边界，不引入通用微服务框架。

### 4.2 会话与 Agent 数据流

```text
用户消息
  → 验证 company/user/token/conversation scope
  → 读取 conversation.active_role
    ├─ CUSTOMER
    │   → 简单意图/是否需要检索
    │   → query rewrite（最多一次）
    │   → hybrid retrieve → RRF → rerank
    │   → citation validation → Groq 回答
    │   → 可回答：保持 CUSTOMER
    │   → 需要业务事实：生成 Handoff，active_role=SUPPORT
    └─ SUPPORT
        → 读取 Handoff + 当前 Evidence + budgets
        → Google 规划下一步
        → 独立 read tools 可并行 / 有依赖 tools 顺序执行
        → 程序校验 Evidence
        → 回答、补问信息、提出 Action、或创建 Engineer Ticket
```

Customer Agent 不调用订单、店铺、连接、发货、库存工具。Support Agent 不重新运行 Customer RAG，也不依赖 Internal RAG。

### 4.3 模型路由（Model Routing）

| 工作类型 | 默认模型 | 说明 |
| --- | --- | --- |
| 普通问答、简单意图分类、简单结构化生成、简单 query rewrite | Groq | 低延迟、低成本路径 |
| Customer RAG 最终回答 | Groq | 只基于通过校验的 Customer 文档上下文 |
| 复杂调查规划、多步推理、复杂 Tool Calling | Google | Support Agent 默认复杂路径 |
| 需要多个独立工具的 parallel tool calling | Google | 模型可提出并行调用，但服务端再次校验依赖与只读性 |
| Evidence 冲突判断、复杂 Action Proposal | Google | 输出结构化分析，不保存隐藏思维过程；最终安全决定仍由普通代码完成 |

实现约束：

- `GROQ_API_KEY`、`GROQ_MODEL`、`GOOGLE_API_KEY`、`GOOGLE_MODEL` 均从 V2 进程环境或 `V2/.env` 读取；Phase 0 不读取、复制或记录现有 Secret 值，V2 运行时也不依赖 `V1/.env`。
- embedding 与 rerank 的 provider/model/dimension 也通过环境变量配置，不能写死模型名。Reranking 按 `building-plan-V2.md` 先接入 `qwen3-rerank`，再用少量 dev cases 比较 Retrieval Quality、Latency 和 Cost；不默认让 Google 主模型承担 Reranker。默认模式依据真实结果选择，集成不可用时如实记录，不能用固定分数冒充。
- `.env.example` 只提供变量名和安全占位符，补齐 Google、LangSmith、embedding/rerank 和预算配置。
- 模型路由先由确定性代码按任务类型决定，不让模型自行选择供应商。
- provider 不可用时返回可观测错误或安全升级；禁止用固定文案伪装真实模型结果。
- 每次调用记录 provider、model、purpose、run ID、latency、token/usage/cost（可获得时）和错误类型；不记录 API Key 或隐藏推理。

### 4.4 并行工具规则

普通代码为可调用工具保存必要元数据：`read_only`、`scope`、`required_inputs`、`depends_on`、`timeout` 和 `budget_cost`。

- 同一步中，输入已知、只读、彼此无依赖、不会争用同一写资源的工具可以并行。
- 例：已知 shop_id 与 order_id 时，读取店铺连接状态和平台订单可以并行。
- 需要上一步返回标识符、版本、映射或授权信息的工具必须顺序执行。
- 写工具从不与依赖它结果的验证并行；顺序始终是版本复核 → 执行 → receipt/reconciliation → Verification。
- 模型提出的并行集合由一个小型确定性执行函数再次检查 Authorization、required inputs、dependency、scope、timeout 和 budget；不建立通用 Workflow Scheduler。
- 并行调用共享总预算，但各自有 timeout；部分失败要作为 Evidence 保存，不能当作空结果。

### 4.5 LLM 与确定性代码边界

LLM 可以理解问题、判断缺失信息、选择只读工具、分析 Evidence、决定下一步调查方向、提出 Action Proposal 和生成用户解释。LLM 不能决定 Authorization、Tenant/Company Scope、Tool/Action Permission、Approval 有效性与过期、版本变化、Idempotency、是否允许执行、执行是否成功、Verification 或 Ticket 是否可关闭。

所有状态修改统一遵循：

```text
Action Proposal
  → Policy Check
  → Human Approval
  → Scope / Version Recheck
  → Execute
  → Receipt / Reconciliation
  → Verification
```

Prompt 不能代替这些检查。分布式写操作只承诺业务上只产生一次有效结果（Effectively-once Business Effect），不宣称严格 Exactly-once Execution。实现只使用 stable idempotency key、duplicate protection、execution claim/lease、request ID、receipt、reconciliation 和 read-after-write verification，不引入大型分布式事务框架。

### 4.6 LangSmith 追踪（Tracing）

所有真实运行至少包含以下层级：

```text
conversation turn
  ├── agent graph/node
  ├── llm call
  ├── retrieval
  │   ├── query route/rewrite
  │   ├── vector search
  │   ├── keyword/BM25 search
  │   ├── RRF
  │   ├── rerank
  │   └── citation validation
  ├── tool call / parallel tool batch
  ├── proposal / approval check
  ├── execution / reconciliation
  └── verification / escalation
```

`LANGSMITH_TRACING`、`LANGSMITH_API_KEY`、`LANGSMITH_PROJECT` 从 `.env` 读取。优先使用 LangChain / LangGraph 已有 integration，只在缺失的 retrieval/rerank/tool 边界加少量 tracing。Trace metadata 包含匿名化的 company/user、conversation/case/action/ticket ID、模型路由原因、工具预算和代码版本；API Key、Authorization Header、Secret、隐藏思维过程和其他公司的敏感数据不得记录。数据库保存可关联的 trace/run ID，使 API、CLI、Ticket 和报告能回溯同一次真实运行。

外部模型或 LangSmith 出现明确的 429、503、配额或服务不可用时，不持续重试，也不伪造远端 Trace。当前 Phase 必须保存本地 run、request、receipt、Evidence、step、错误类型和可用 trace metadata，并把远端验收明确标为 `External Acceptance Deferred`。真实 LLM 与远端 LangSmith 的集中补验收放在 Phase 9/最终验收；外部服务限制不再单独阻塞后续本地迁移。

### 4.7 测试调用策略

- Unit、contract、graph 和 regression tests 默认使用 deterministic mock/stub，并关闭 LangSmith tracing；这些测试不消耗 Groq/Google 配额，也不上传测试 Trace。
- 普通 Phase 可以保留少量成功的真实 LLM + LangSmith 验收，但不把外部 429/503 作为迁移硬阻塞。Phase 4 尚缺的 3 条真实调查、不同工具路径、提前停止/预算行为和远端 Trace 统一延后到 Phase 9/最终验收。
- 完整 Agent Evaluation、重复运行和大规模对照统一保留到 Phase 9，并继续遵守 Dry Run、调用量/Token/费用/时间估算和用户确认门禁。
- Doctor/capability check 只在模型、SDK、endpoint、workspace 或相关环境配置变化后重新运行，不作为普通回归命令。
- Support 调查默认使用 `GOOGLE_MODEL`。只在主模型经过有限重试后仍返回临时 `503 UNAVAILABLE/high demand` 时，才使用 `GOOGLE_FALLBACK_MODEL`；权限、配额、输入、模型输出质量或业务判断问题不得触发 fallback。每次真实调用记录实际 provider/model。

## 5. 影响面

### 5.1 数据库（Database）

V2 使用一套 PostgreSQL + pgvector 和事实来源指定的顺序 SQL migration。数据库不在 Phase 0 一次性完整设计，而是随纵向切片增加：

- `db/001_support.sql`：Task 01 当前需要的身份、资料、会话、运行记录和 checkpoint。
- `db/002_orders.sql`：Task 04 的平台订单、接收记录、任务和商家订单。
- `db/003_actions.sql`：Task 06 的 Proposal、Approval、Execution、receipt 与 Verification。
- `db/004_shipments.sql`：Task 07 的送仓、仓库订单、出库、发货事件和平台结果。
- `db/005_stock.sql`：Task 12 的库存事实与发布记录。
- `db/006_tickets.sql`：Task 13 的 Ticket、分配和后续 recheck 所需记录。

具体表和字段只在对应 Task 实施时根据真实查询与安全约束确定，不提前创建“未来可能需要”的表。

迁移策略：

- V1 数据和 migration 完全不变，也不迁移到 V2。V2 使用自己的数据库对象和 bootstrap 数据。
- migration 必须能从空数据库运行，也能从上一 Phase 顺序前进；只创建当前纵向切片需要的对象。
- V2 migration 与 bootstrap 分离；初始化服务负责 schema，lab bootstrap 只负责明确标记的测试数据。

### 5.2 LangGraph（Graph）

需要重写 Graph State 和路由：

- State 新增 company/user/shop scope、active_role、handoff、case_type、evidence、budgets、pending proposal、execution receipt、verification 和 escalation reason。
- Customer subgraph 仅负责对话/RAG/Handoff。
- Support subgraph 负责调查计划、动态工具、Evidence 校验、行动建议、HITL、执行、Verification 与 Escalation。
- Handoff 是同一 conversation 的状态转换，不是 Ticket ID。
- Approval interrupt 继续使用持久 checkpoint；resume 前重新验证 actor、过期时间、对象版本和作用域。
- Ticket 是终止/人工接手分支，不再触发 Support Agent。
- 重启后由稳定 conversation/thread/action ID 恢复，不依赖内存对象。

### 5.3 API

V2 不修改或兼容 V1 `/support/*`。API 随当前纵向切片最小增加，可能包括：

- conversation/messages：创建会话、发送消息、读取 active role、时间线和引用。
- cases/evidence：读取当前 Support 调查状态、工具记录和缺失信息。
- actions/approvals：读取 proposal、批准/拒绝、恢复执行、查看验证结果。
- tickets：创建/查询、工程师最小权限读取、recheck 和导出。
- lab/operator：创建场景、注入允许的故障、恢复连接；与 support API 隔离且不能暴露给商家身份。
- health/doctor：依赖、migration、pgvector dimension、模型和服务可达性检查，不输出 secret。

不在 Phase 0 预建完整 API。到对应 Phase 时，DTO 使用资源 ID 与显式状态，前端不能依据自然语言判断流程状态；写接口带 actor identity，并在存在重复写风险时使用 idempotency key。

### 5.4 前端

可保留 Vite/React/Tailwind 构建底座，但页面和状态管理要重新设计：

- 商家视图：连续会话、Customer/Support 当前角色提示、RAG 引用、补问信息、Action Proposal 与批准/拒绝。
- Support 调查时间线：Evidence、工具调用、矛盾、预算、Verification；只展示允许用户看到的字段。
- 工程师视图：只展示已分配 Ticket，区分事实/推测/已排除/缺失信息，支持 recheck。
- 测试操作员视图或 CLI：场景 bootstrap、连接故障/恢复；不能与商家 UI 共用授权。
- 移除 URL customer 白名单、固定四客户 selector 和旧 Support demo toggle。
- 前端只消费 API，不复刻 Graph 决策、批准策略或成功判定。

### 5.5 测试与评估

V1 测试保持原样且不作为 V2 回归集。V2 只为当前真实风险逐步增加必要测试：

1. unit：模型路由、query rewrite 次数、RRF、引用校验、Evidence、预算、policy、状态机。
2. contract：platform/merchant/warehouse HTTP schema、鉴权、错误码、timeout 与 idempotency。
3. integration：真实 PostgreSQL/pgvector、worker、事件处理和跨服务状态变化。
4. graph：Customer → Handoff → Support 粘滞、动态工具顺序、parallel/sequential、interrupt/resume。
5. recovery：响应丢失但远端已提交、崩溃恢复、lease 过期、并发 worker、重复事件。
6. security：跨公司/店铺、伪造 approval、恶意文档/日志、未分配工程师、lab 权限泄露。
7. end-to-end：订单、发货、库存、人工 Ticket 三条主线。
8. eval：50 dev + 30 frozen holdout；retrieval、tool strategy、role architecture 对照，共 176 次指定运行。

每个阶段只运行本阶段新增测试和已经完成的 V2 回归集，不追求为了 Coverage 数字堆重复测试。V1 测试不修改、不删除。

## 6. 渐进式实施阶段

### Phase 0 — 基线冻结与合同清单

目标：在完全不修改 V1、building plan 和任何运行代码的前提下，建立 V2 的实施门禁。

工作：

- 记录 V1 只读参考范围和开始时的 Git 状态；不执行会在 V1 目录产生 cache、报告或数据库变化的命令。
- 把 `building-plan-V2.md` 的 R01–R14、Task 01–15 转成验收矩阵。
- 固定 Source of Truth 优先级、V2 独立运行边界、简单实现原则、Phase 审批门槛和报告模板。
- 记录后续实现需要遵守的模型路由、LLM/普通代码边界、工具并行规则、业务所有权和 LangSmith 元数据；具体 API/DB 字段留到对应 Task。
- 明确 Phase 1 只做 Task 01 的终端问答纵向切片，不提前实现后续工具、业务表或前端。

验证：`building-plan-V2.md` 内容哈希不变；V1 Git 状态与 Phase 0 开始时一致；只新增/修改 V2 计划和 Phase 0 文档。  
可运行结果：Phase 0 没有产品运行时代码；可通过 Git/文档检查重复验证事实来源、范围和下一 Phase 入口。这是唯一的实施前门禁，产品 Vertical Slice 从 Phase 1 开始。  
退出条件：验收矩阵、实施规则和 Phase 0 报告完成；明确停在 Phase 0 等待用户确认。  
V1：不修改、不删除、不移动、不重命名。

### Phase 1 — 终端 Customer RAG 第一条真实纵向切片

对应：Task 01，以及本轮新增模型路由要求。

工作：

- 按 Task 01 创建 `pyproject.toml`、锁定依赖、`.env.example`、`.gitignore`、`compose.yaml`、CLI、配置、数据库、身份、模型、Customer Agent、Graph 和 tracing。
- 创建 `db/001_support.sql`、bootstrap、12 篇短产品资料、Customer prompt、6 个 dev 问答案例和当前 README。
- 建立 company/user/token scope、conversation/message/agent_run、资料与 checkpoint 当前所需的最小数据模型。
- 实现确定性 Model Router：Task 01 普通问答、简单结构输出和 Customer RAG 回答使用 Groq；同时用 doctor 验证已配置 Google 模型的 structured output/tool calling 能力，但不让 Customer Agent 使用业务工具。
- 接入 PostgreSQL + pgvector 的真实 embedding/retrieval、Groq 回答、引用、会话保存和 LangSmith trace。
- 本阶段只提供 building plan 要求的 CLI，不提前创建 React 或完整业务 API。

测试：资料重复导入、无效 token、公司隔离、conversation continuation、router 选择、配置缺失、secret redaction 和 6 个真实问答。  
可运行结果：终端问题 → 身份映射 → PostgreSQL/pgvector 资料 → Groq → 回答与引用 → 保存会话/用量 → 终端显示。  
退出条件：doctor 确认实际模型与向量调用、向量长度 1024；Groq 问答真实运行，Google 能力检查真实运行；LangSmith 可看到关键 trace；任何日志/trace 不含 Key。  
V1：只读参考，不复制旧业务语义，不产生运行依赖。

### Phase 2 — Customer Agent 与完整 Customer RAG

对应：Task 02–03。

工作：

- 扩展 Task 01 的 Customer 文档版本、company/product 可见性和过期规则。
- 实现 intent/query routing、最多一次 rewrite、vector + 中文 keyword/BM25、RRF，并按 building plan 接入 `qwen3-rerank`。
- 实现 citation existence、authorization、version 和 semantic claim support 校验。
- Customer Graph 只包含澄清、检索、回答和需要 Support 的判断，禁止注册业务工具。
- 支持 `vector_only`、`hybrid`、`hybrid_rerank` 三种评估模式。

测试：访问隔离、版本冲突、无答案、恶意文档、改写次数、RRF、rerank failure、引用 claim 不支持；从 Phase 1 的 6 个 dev cases 持续扩充。  
可运行结果：CLI 或当时已经存在的入口能完成带真实引用的中文自助问答；无法回答时只产生 Handoff 意图，不读取后台事实。完整 API/React UI 保持在 Phase 8。  
退出条件：三种检索模式均真实运行且可追踪；程序校验失败时不能输出确定性答案。  
边界：不加入 Internal RAG、Customer 后台工具或第二个向量数据库。

### Phase 3 — 电商业务模拟器与订单真实数据流

对应：Task 04。

工作：

- 建立 platform、merchant、common contracts、worker 和 lab CLI。
- 建立订单、事件、接收记录、处理任务与商家订单表。
- 平台创建有效已支付订单，经 HTTP 事件和 worker 驱动商家订单生成。
- 实现重复事件幂等、乱序/延迟/timeout/失败状态及独立检查器。
- Compose 增加服务和 init/bootstrap，不向 support 服务挂载隐藏答案。

测试：正常订单、丢事件、重复事件、处理失败、跨公司、重启后继续处理；HTTP contract 与数据库最终事实同时验证。  
可运行结果：不经过 Agent 也能用 lab 命令制造和观察真实“平台有单、商家无单”故障。  
退出条件：故障由真实状态演化产生，不由 scenario oracle 字符串返回。  
边界：V2 不引入旧 ResolveLab 预置客户分支。

### Phase 4 — Handoff、Support Agent 与动态订单调查

对应：Task 05，并落地 parallel tool calling。

状态：**Implementation Complete / External Acceptance Deferred**。实现、离线测试和一次 `gemini-3.6-flash` 最小真实 Tool Calling 已通过；3 条完整真实调查和远端 Trace 因外部 503/429 延期，不伪装为已验收。

工作：

- 定义结构化 Handoff：用户原话、已确认目标、已知标识符、Customer RAG 结论/引用、缺失信息，不包含编造业务事实。
- 实现 conversation.active_role 原子切换和多轮 Support 粘滞。
- 实现 `get_shop_status`、`get_order`、`get_process_records`、`check_connection` 等只读工具。
- Support Graph 使用 Google 做动态下一步规划；每次依据现有 Evidence 决定下一工具、补问、停止或升级。
- 用普通函数校验 tool metadata，并以简单并行执行层处理独立只读调用；不建立通用 Workflow Scheduler。
- 从开发案例量化最大工具数、总时长、连续错误数，不使用无限循环。

测试：不同故障产生不同工具顺序、可提前停止、缺 order/shop ID 先补问、独立工具并行、有依赖工具顺序、预算耗尽安全停止、handoff 后不回 Customer、无 Internal RAG。  
可运行结果：订单故障可被真实工具调查并产生可核验 Evidence，但暂不自动写入。  
退出条件：至少两个案例显示不同工具路径，一个案例安全提前停止，一个案例触发预算升级。  
边界：V2 不实现旧 Ticket 交接、固定工具扇出或 internal knowledge tool。

### Phase 5 — 订单恢复、批准、执行与验证

对应：Task 06。Task 10 的响应丢失和崩溃恢复必须等 Phase 6 的两条恢复主线完成后再实现。

状态：**Complete**。订单 Proposal → Approval → Scope/Version Recheck → Execution → Receipt/Reconciliation → Verification 已通过离线回归和本地真实 HTTP/Worker 纵向案例；完整 response-lost、worker crash 和并发 lease 回收仍按计划留在 Phase 6。

工作：

- 建立 recover_order proposal、10 分钟 approval、对象/版本/公司/店铺作用域。
- Approval 后先复核当前版本，再创建 execution claim/lease，由 worker 执行真实恢复。
- 为后续恢复保存 step、远端 receipt、request ID、reconciliation、稳定 idempotency key 和 execution claim/lease 基础记录；本阶段不实现完整 response-lost、worker crash 或并发恢复。
- Verification 独立读取平台与商家事实，比较唯一性、商品、数量、金额；模型文字不能决定成功。
- Support Graph interrupt/resume 与 stable action ID 接通。

测试：批准/拒绝/过期/伪造批准、批准前重启、批准后版本变化、重复点击、重复订单防护、验证失败不得报成功。  
可运行结果：订单恢复完整走 Proposal → Approval → Execution → Verification。  
退出条件：稳定 idempotency key 和重复保护保证 Effectively-once Business Effect；成功只能由确定性 Verification 判定。  
边界：V2 不实现旧通用 retry action 或旧导出验证器。

### Phase 6 — 发货数据流、调查、受控恢复与恢复韧性

对应：Task 07–10。先完成 Task 07–09 的发货主线，再用 Task 10 同时覆盖订单和发货恢复的 response lost、worker crash 与并发执行。

工作：

- 建立仓库订单、dispatch、shipment、事件与平台回传状态。
- 商家系统通过正常业务任务请求仓库发货；Agent 不提供“命令仓库重复出库”的工具。
- 增加发货调查工具和 Evidence 对齐：来源、对象、时间、版本、矛盾。
- 实现 recover_shipment proposal/approval/execution/reconciliation。
- Verification 比较商家、仓库、平台三端；15 秒内不能证明则返回 pending/待复查，不宣称成功。
- 在两条恢复主线都存在后实现 execution claim/lease、稳定 request ID、receipt/reconciliation，以及远端提交后 response lost 和实际子进程崩溃恢复。

测试：不同失败步骤、重复事件、重复出库防护、响应丢失、崩溃恢复、三端冲突、pending 窗口、跨店铺。  
可运行结果：第二条主线完整接通，且不会为了修平台状态再次实际出库。  
退出条件：所有写动作均有批准、幂等、receipt/reconciliation 和独立验证；重复请求、timeout、response lost、worker crash、重复点击和并发执行只产生一次有效业务结果。  
边界：不承诺严格 Exactly-once Execution，也不直接修改平台/仓库数据库来模拟成功。

### Phase 7 — 多店铺/渠道、错误预算与库存只读调查

对应：Task 11–12。

工作：

- 贯通多公司、多店铺、多渠道 token scope 与 401/403/429/500/503/timeout 映射。
- 实现受控测试操作员 connection restore；Support/商家身份不可调用。
- 建立 stock facts、正常发布任务和只读 `get_stock_facts`。
- 实现预期上架量公式、时间窗口和版本对齐；未知商品关系不做数字比较。
- 统一错误/工具预算与 Escalation reason。

测试：全部错误码、重试上限、timeout 不当空数据、跨租户、正常库存差异、版本仍在窗口、真实推送失败、无库存写工具。  
可运行结果：订单/发货在复杂连接状态下安全停止；库存只能解释或升级。  
退出条件：任何身份和模型都无法取得库存写能力。  
边界：不实现绕过服务鉴权的测试后门或无限重试。

### Phase 8 — Engineer Ticket、最小 API 与前端演示

对应：Task 13，以及本轮明确的作品集 API/前端展示要求。Task 15 的 recheck 仍留在 Phase 10。

工作：

- 仅在未解决、达到预算、用户明确要人工时创建 Ticket；同一会话不重复创建未关闭 Ticket。
- Ticket 保存 Customer Handoff、Support Evidence、已尝试操作、事实/推测/已排除/缺失信息和下一检查。
- 实现真实 assignment rule 与 engineer 最小读取作用域。
- 完成 versioned API、OpenAPI contract 与 React 商家/调查/批准/工程师页面。
- CLI、API、UI 调用同一组普通业务函数，不维护三套逻辑，也不增加复杂前端状态管理或大型 Design System。

测试：直接请求人工、预算升级、Evidence 不足、未知异常、无重复 open Ticket、assignment、未分配/其他公司拒绝、前端 build 和关键 E2E。  
可运行结果：三类身份能通过自己的入口完成 V2 全流程。  
退出条件：Ticket 不再出现在 Customer → Support 主路径；前端不依赖 V1 DTO。  
边界：V2 不复制旧 Support/Customer toggle、旧 customer selector 或旧 Ticket API/模型/组件。

### Phase 9 — 评估、对照实验、报告与验收门禁

对应：Task 14。

状态：**Preparation Complete / Formal Benchmark Deferred**。Dataset、Eval Runner、对照配置、3-case validate-only Dry Run 和预算估算保留；当前不运行付费 Benchmark、176 次对照、Holdout 30×3 或批量外部模型/LangSmith 验收，并且这些运行不阻塞 Phase 10。

工作：

- 在前面阶段持续累积到 50 dev + 30 frozen holdout，隔离 hidden facts。
- 完成三种 retrieval、fixed vs dynamic tools、single vs dual roles 对照。
- 先完成 Dataset、Eval Runner、Dry Run、运行次数统计、Token 预计用量、预计费用和预计时间。
- 将估算与 Dry Run 结果保留为评估基础。正式运行已按当前测试策略延期，除非用户以后明确改变要求，否则不执行。
- 从 LangSmith/本地运行记录聚合成功、失败、正常无需修复、人工升级、工具数、无效调用、延迟、token/usage/cost。
- 对假成功、越权、未批准写、重复业务效果单列安全失败。
- 输出 JSON/HTML，可追踪到 case/run/trace/version。

测试：指标分母、重复次数、失败计数、无价格配置、报告脱敏、holdout 隔离和安全门禁。  
可运行结果：可重复执行确定性 Dry Run 并得到预算估算；它不代表模型质量结果。
退出条件：评估基础、门禁和失败保留逻辑已完成；正式 Benchmark 当前不作为 Phase 10 前置条件。
边界：V1 的旧数据集和报告保持不变，但不作为 V2 数据或结果。

### Phase 10 — Ticket 复查、文档与 V2 独立交付

对应：Task 15 和最终检查清单。

状态：**Complete**。Ticket 复查/关闭、API/CLI/React 展示、HTML 导出、一次性 Compose init、V2 独立性检查和本地固定回归已完成；没有调用真实 Groq/Google 或上传 LangSmith Trace。

工作：

- 完成 V2 README、架构、数据流、模型路由、环境变量、doctor、运行/测试/eval 命令和限制说明。
- 实现 order/shipment/stock Ticket recheck，复用同一确定性 Verification；无业务标识符时返回 `NEEDS_INFO` 并保持工单打开。
- 完成 compose init、最小服务环境、全新 volume 启动与三条 demo。
- 验证 support 镜像/容器看不到 lab hidden facts、故障控制或 holdout answers。
- 更新 CI：unit → contract → integration → frontend build → security → selected E2E。
- 生成最终 versions、已知限制、实际评估报告和短演示材料。
- 验证 V2 的 import、配置、容器、数据库初始化、测试和文档均不引用 V1 目录；V1 原样保留在 GitHub 中作为历史版本。

测试：全新目录/容器卷冷启动；三条 demo；Ticket recheck；全套安全回归；secret scan；README 按步骤复现。  
可运行结果：只靠 V2 README 可以在干净环境完成自助问答、批准恢复、人工接手/复查。  
退出条件：V2 最终 checklist 全部有真实证据；未接真实外部平台、小样本评估等限制明确写出。  
V1：不修改、不删除、不移动、不重命名、不归档。

## 7. Task 01–15 与实施阶段映射

| V2 building plan | 实施阶段 |
| --- | --- |
| Task 01：第一条 Customer RAG 纵向切片 | Phase 1 |
| Task 02–03：混合检索、重排与引用 | Phase 2 |
| Task 04：订单业务流 | Phase 3 |
| Task 05：Handoff/Support 调查 | Phase 4 |
| Task 06：订单恢复 | Phase 5 |
| Task 07–09：发货、调查、恢复 | Phase 6 |
| Task 10：响应丢失/崩溃/并发 | Phase 6，在 Task 09 后 |
| Task 11：多店铺/渠道/错误 | Phase 7 |
| Task 12：库存只读 | Phase 7 |
| Task 13：工程师 Ticket | Phase 8 |
| Task 14：评估 | Phase 2 起持续积累，Phase 9 汇总 |
| Task 15：交付/recheck/report | Phase 10 |

## 8. 每阶段统一完成定义（Definition of Done）

Phase 0 是只读实施门禁，不创建产品运行时代码；其完成条件写在 Phase 0。Phase 1–10 只有同时满足以下条件才算完成：

- 本阶段的垂直数据流从入口到持久化/外部服务/验证真实接通，没有固定答案、空 tool 或假 success。
- migration/bootstrap 可在空数据库运行，也可在上一阶段数据库上前进。
- 只新增当前风险需要的 unit、contract、integration、graph、recovery、security 或关键 E2E 测试；此前 V2 回归继续通过。
- 本地运行必须保存 Agent、LLM、RAG/rerank、tool、write、verify 的 run/request/Evidence/receipt/step 与可用 trace metadata。远端 LangSmith 可用时验证可查询；遇到明确 429/503 时标记 `External Acceptance Deferred`，集中到 Phase 9/最终验收，不阻塞后续本地迁移。
- CLI/API/UI 中已经存在的入口使用同一组普通业务函数与状态定义。
- 文档更新到本阶段真实能力，不提前宣称后续功能完成。
- V2 内本阶段产生的临时代码及时清理；V1 完全不触碰。
- API Key、授权 token、hidden eval facts、其他租户内容没有出现在日志、trace、报告、截图或仓库。
- 没有为了未来可能需要而增加复杂基础设施或抽象层。

每个 Phase 完成后立即停止，报告：完成内容、主要文件、真实数据流、测试结果、LangSmith Trace、使用模型及原因、已知问题和下一 Phase。未经用户确认不得进入下一 Phase。Phase 9 的完整 Benchmark 还需要单独的运行前确认。

## 9. 已锁定决策与实施前检查点

已锁定：

- `building-plan-V2.md` 是业务、架构和验收唯一事实来源。
- `refactor-plan.md` 只是实施计划；发生冲突时立即按 building plan 修正，不反向修改 building plan 迁就实现。
- 本轮指定的 Groq/Google 模型路由是模型层最终要求；旧计划中的单供应商模型假设不再采用。
- Customer Agent 只做 Customer RAG；Support Agent 只做动态业务调查。
- Handoff 负责 Agent 交接；Ticket 只负责人工接手。
- 库存只读，没有自动库存修复。
- 恢复成功只能由确定性业务 Verification 证明。
- 写操作遵循 Proposal → Policy Check → Human Approval → Scope/Version Recheck → Execute → Receipt/Reconciliation → Verification。
- 目标是 Effectively-once Business Effect，不承诺严格 Exactly-once Execution。
- V1 永久只读保留；V2 是独立交付目录，不能 import 或依赖 V1。
- 优先使用普通 Python 模块、函数和明确数据模型；不主动增加大型基础设施和多层抽象。

实施开始前需要做但不阻塞本计划的检查：

- 对当前 Groq/Google 模型验证 structured output、tool calling、parallel tool calling 和上下文限制；结果写入 `docs/versions.md`，模型名仍只来自 `.env`。
- 对选定 embedding dimension 和 pgvector schema 做 doctor 检查；dimension 不匹配时 fail fast。
- 按 building plan 接入 `qwen3-rerank`，再用少量 dev cases 比较 Retrieval Quality、Latency 和 Cost；不默认用 Google 主模型做 Reranker。
- Phase 0 只记录 V1 的只读状态，不运行或修改 V1 测试。
- Phase 9 先完成 Dry Run 和成本/时间估算，获得用户确认后才运行完整 Benchmark。

## 10. 推荐执行顺序

严格按 Phase 0 → 10 前进。最关键的依赖顺序是：

```text
身份/会话/追踪
  → Customer RAG
  → 真实业务服务
  → Handoff + 动态调查
  → 受控写入 + 独立验证
  → 韧性/库存/人工接手
  → 完整 UI/API
  → 评估与 V2 独立交付
```

先让 Task 01 的终端问答形成第一条真实纵向切片，再增加检索能力和业务状态；先接只读调查，再接写动作；先有 Proposal/Policy/Approval/版本复核/幂等与 Verification，再允许恢复。每次只完成一个 Phase，报告后停下等待确认。
