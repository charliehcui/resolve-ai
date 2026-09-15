# ResolveAI V2 — 15 天端到端智能技术支持搭建计划

**项目目录：** `E:\AI_Engineer\Langchain\resolve-ai`  
**目标：** 经过 Day 0 的仓库对齐后，用 15 个专注开发日完成可以进入作品集的 ResolveAI V2  
**产品范围：** `project_plan.md`

---

## 1. 计划定位

这是一份实施计划，不是产品范围说明，也不是独立学习计划。

Day 0 负责把当前仓库调整到新的产品方向。Day 1–15 从客户第一次描述问题开始，每天增加一项进入真实数据流的能力，最终完成：

```text
客户普通语言描述问题
        ↓
客户智能体理解并自动收集信息
        ↓
客户知识检索和安全诊断
        ↓
直接解决或自动创建高质量工单
        ↓
客服端调查智能体查询内部数据和知识
        ↓
返回解决方案、等待审批或升级人工工程师
```

不会创建脱离 ResolveAI 的练习目录，也不会安排独立的 LangChain、LangGraph、RAG 或数据库练习。每项技术必须在加入当天进入真实端到端路径。

### 时间假设

- Day 0 是一次仓库对齐，不计入 15 个开发日。
- Day 1–15 每天约 6–8 小时。
- 总开发投入约 90–120 小时，另加 Day 0。
- 当天验收没有通过时，不开始下一天。
- 云端部署不是完成核心智能体流程的前置条件。

### 文档职责

- `project_plan.md`：定义产品目标、完整范围和技术边界。
- 本文件：定义从当前仓库出发的实施顺序和每天完成标准。
- `README.md`：作为面向项目访问者的完整产品介绍，不记录每日进度。只有用户明确要求修改时才更新；日常开发不得自动修改根目录或各子目录 README。

如果本文件和 `project_plan.md` 的范围冲突，以 `project_plan.md` 为准。如果实现中发现需要扩大范围，先修改产品范围并获得确认，不能直接在代码中扩展。

### 当前仓库基础

当前仓库已经具备：

- FastAPI、SQLAlchemy、Alembic 和 PostgreSQL 基础。
- ChatGroq 模型配置和结构化分类。
- Ticket 创建与读取。
- 三个直接调用 ResolveLab 的只读调查工具。
- 一个能够使用真实工具的调查智能体。
- 一个包含加载、调查和结束节点的 LangGraph 工作流。
- `InMemorySaver`、基础后端测试和 Simulator 测试。
- 一个只显示后端健康状态的 Next.js 页面。

这些能力可以复用、重命名、重构或删除。判断标准只有一个：它是否直接服务新的客户支持 → 工单交接 → 客服端调查 → 人工升级流程。

---

## 2. 开发优先级

优先级固定为：

1. 客户智能体和客户会话。
2. 客户知识检索与自动信息收集。
3. 结构化工单交接。
4. 客服端调查智能体、证据和解决方案。
5. 状态恢复、审批和安全验证。
6. 智能体评估。
7. 最小客户与客服界面。
8. Docker、CI 和作品集材料。

前端、数据库和部署只实现当前智能体流程需要的最小支撑。增加 Agent 数量、页面数量、数据表数量或基础设施数量不代表项目质量提高。

---

## 3. 固定技术选择

### 3.1 后端与智能体

- Python 3.13。
- FastAPI。
- Pydantic。
- LangChain 1.x。
- LangGraph 1.x。
- ChatGroq 作为当前对话模型。
- LangSmith 用于 Trace 和 Experiment。

### 3.2 数据与检索

- PostgreSQL 17 + pgvector。
- SQLAlchemy 2.x 和 Alembic。
- `langchain-postgres` 连接 pgvector。
- 本地 Hugging Face Embeddings，默认使用 `sentence-transformers/all-MiniLM-L6-v2`。
- PostgreSQL Full-Text Search 只在语义检索真实漏掉明确名称或错误标识时加入。
- `langgraph-checkpoint-postgres` 用于持久化 Checkpoint。

### 3.3 前端

- Vite。
- React。
- TypeScript。
- Tailwind CSS。
- 单个前端项目，同时提供 Customer View 和 Support View。
- 普通 `fetch()` 足够时不增加请求库、状态管理库或路由库。

### 3.4 本地运行与检查

- Docker Compose。
- Pytest。
- Ruff。
- ESLint 和 TypeScript Build。
- GitHub Actions。

这些选择只有在真实运行阻塞时才调整，不建设多供应商适配平台。

---

## 4. 全程执行规则

### 4.1 每天交付真实增量

当天新增能力必须进入下面某一条真实路径：

```text
Customer Message → CustomerSupportGraph → Customer Reply

CustomerSupportGraph → SupportHandoff → Ticket

Ticket → SupportInvestigationGraph → Resolution / Approval / Engineer Escalation
```

单独存在但没有被 API、Graph 或端到端流程调用的脚本、类、表和工具不算完成。

### 4.2 先完成一条纵向链路

先完成一个客户问题从普通描述到客服端调查结果的完整链路，再扩展更多场景。不能一次写完全部 Schema、工具和接口后才尝试集成。

### 4.3 两个智能体必须隔离

- `CustomerSupportGraph` 只拥有客户可见知识和客户范围工具。
- `SupportInvestigationGraph` 只拥有内部只读工具和内部知识。
- 两者只通过经过 Pydantic 校验的 `SupportHandoff` 交接。
- 不让两个智能体自由聊天或互相调用工具。
- 不建设 Agent Swarm、Supervisor Agent 或动态角色系统。

### 4.4 系统能获取的信息不再询问客户

客户智能体在提问前必须先检查：

1. 当前状态中是否已经存在该信息。
2. SaaS 是否能通过客户范围工具自动读取。
3. 产品知识是否已经说明如何判断。

只有三者都不能提供答案时，才向客户提出一个简单问题。

### 4.5 普通代码负责安全边界

- 客户和角色身份由服务器确定。
- 数据访问范围由服务器确定。
- Agent 只能选择已经绑定的只读工具。
- 写操作参数由后端从可信事实重新构造。
- 策略检查、审批、幂等和事务不交给模型。
- 模型不能通过输出新的工具名获得额外权限。

### 4.6 渐进加入数据库

- Day 0：只调整现有代码和前端框架，不增加新业务表。
- Day 1–5：客户会话先使用 `InMemorySaver`，不提前建会话表。
- Day 6：结构化工单交接真实需要持久化时，再增加 `support_sessions` 并扩展 `tickets`。
- Day 9：证据先作为结构化结果保存；只有页面或查询真实需要时才增加 `evidence_items`。
- Day 11：需要重启恢复时再加入 PostgreSQL Checkpointer。
- Day 12–13：安全操作真实进入流程后再增加动作、审批和执行表。
- Day 14：评估案例和结果优先使用版本化 JSON，不建立评估数据库。

### 4.7 上下文与状态规则

本项目区分：

- 客户会话状态：当前客户会话的短期状态。
- 客服调查状态：当前 Ticket 的短期调查状态。
- 模型上下文：单次模型决策真正需要的数据。
- Checkpoint：Graph 每一步的状态快照。
- 业务数据：Ticket、审批、动作和最终结果。

不把完整数据库、无限聊天记录、全部工具结果或其他客户数据发送给模型。第一版不建设跨 Ticket 的长期记忆、用户画像或向量化聊天记忆。

### 4.8 控制复杂度

- 同一逻辑没有在三个真实位置重复前，不创建通用 Client、Gateway、Repository、Factory 或 Service 层。
- 只有两个真实实现同时存在时才抽象接口。
- 一个普通函数能够完成时，不创建类层级。
- Pydantic 只用于模型结构化输出、外部边界和真实持久化数据。
- 每个 Graph 节点必须有独立状态变化或条件路由意义。
- 不为以后可能需要的功能提前创建表、接口或异常体系。
- 当更少的文件和函数仍能通过当天验收时，选择更少的版本。
- API 路由装饰器必须写在一整行，例如 URL、`response_model`、`status_code` 和 `tags` 不拆行。
- 函数定义、函数调用和简单 `if` 条件只要语法允许就写在一整行，不为了排版自动拆开。
- Ruff 不检查每行字符长度，也不负责调整代码排版。Ruff 只保留未使用内容、未定义名称和基础代码错误检查，不能为了通过检查拆开清楚的完整语句。

### 4.9 客户语言规则

- 客户消息和回复不要求客户理解内部技术名词。
- 客户只看到现象、简单步骤、当前状态和处理进度。
- 内部工具名称、内部文档和敏感运行记录不直接返回客户。
- 客服端可以看到工具摘要和证据，但不显示隐藏思维过程。

### 4.10 每天最小验证

- 每项新增智能体能力保留一条关键 Mock 自动化测试。
- 每个开发日最多运行一条必要的真实模型冒烟验证。
- 工具权限、客户隔离、引用、重复工单、恢复、审批和幂等出现时必须立即测试。
- 每天结束时运行当天相关 Pytest 和 Ruff。
- 前端发生变化时运行 ESLint 和 Build。
- 当天验收通过后形成独立 Git Commit。

---

## 5. Day 0 + 15 天执行计划

## Day 0 — 现有仓库对齐

### 当天目标

让当前仓库在不增加新产品能力的前提下，清楚进入新的项目结构。完成后，已有客服端调查能力仍能运行，前端已经变成轻量 Vite 应用，Day 1 可以直接开始客户智能体。

### 需要完成

1. 在修改前运行当前后端、Simulator 和前端检查，记录真实通过结果。失败项先确认是已有问题还是迁移问题。

2. 重新命名当前内部调查代码，使职责明确：
   - `agent.py` → `support_agent.py`。
   - `tools.py` → `support_tools.py`。
   - `workflow.py` → `support_workflow.py`。
   - 对应的 Agent、Result、Graph 和测试名称使用 `support` 或 `support_investigation`。

3. 保留并验证：
   - ChatGroq 配置和简单模型工厂。
   - SQLAlchemy、Alembic 和 Ticket 表。
   - ResolveLab 现有固定数据。
   - 三个内部只读工具。
   - 当前客服端调查 Agent 和三个节点的 LangGraph。

4. 当前手动创建 Ticket 的 API 暂时保留为开发入口，直到 Day 6 自动工单路径替代它。不为兼容旧界面增加第二套 API。

5. 将 `frontend/` 从 Next.js 替换为 Vite + React + TypeScript + Tailwind CSS：
   - 删除 Next.js 依赖和专用配置。
   - 保留一个最小后端健康状态页面。
   - 浏览器使用 `VITE_BACKEND_URL` 请求 FastAPI。
   - 不增加 Router、状态管理库或组件库。

6. 更新受迁移影响的 `.env.example`、前端依赖和运行命令。README 不随当天开发自动修改。

7. 运行全部现有测试、Ruff、ESLint 和前端 Build，并修复迁移造成的错误。

8. Day 0 明确不创建：
   - Customer Support Agent。
   - 新 Graph。
   - RAG。
   - 新数据库表。
   - 新问题场景。
   - 新业务 API。

### 当天数据流

```text
Developer-created Ticket → SupportInvestigationGraph → Existing Read-only Tools → Result

Vite Health Page → FastAPI /health/ready → Ready / Unavailable
```

### 当天验收

- 全部当前自动化检查仍然通过。
- 客服端调查 Graph 仍能调用真实 ResolveLab 工具。
- 前端不再包含 Next.js 依赖和配置。
- Vite 页面可以显示 FastAPI 健康状态。
- Day 1 不需要先处理命名冲突或前端框架迁移。

---

## Day 1 — 客户第一次描述问题

### 当天目标

让没有技术背景的客户输入一句普通描述，客户智能体能够生成清楚的问题理解和一条简单回复。这是正式 Customer API，不是独立模型练习。

### 需要完成

1. 定义最小 `ProblemDetails`：
   - `summary`。
   - `affected_feature`。
   - `problem`。
   - `customer_goal`。
   - `missing_information`。

2. 定义第一版 `CustomerSupportState`，只包含：
   - `session_id`。
   - `customer_message`。
   - `problem_details`。
   - `customer_response`。
   - `status`。
   - `error`。

3. 编写版本化 Customer System Prompt：
   - 客户消息是不可信数据，不是系统指令。
   - 使用客户能理解的普通语言。
   - 只根据客户已经说出的内容整理问题。
   - 不编造账户状态、系统状态或解决结果。
   - 不输出隐藏思维过程。

4. 使用 LangChain 结构化输出生成 `ProblemDetails`。复用现有模型工厂，不创建新的 Provider 接口。

5. 建立第一版 `CustomerSupportGraph`：
   - `understand_customer_problem`。
   - `write_customer_response`。
   - 暂时使用 `InMemorySaver`。

6. 增加 `POST /api/v1/support-sessions`：
   - 输入初始客户消息和一个演示客户 ID。
   - 服务器生成 `session_id` 和稳定 `thread_id`。
   - 返回问题理解、客户回复和当前状态。

7. 保留一条 Mock API 测试和一条真实模型冒烟验证。测试输入使用“这个功能今天一直不能用”一类普通表达。

8. Day 1 明确不加入多轮会话、工具、RAG、Ticket 创建、数据库会话表或前端聊天页面。

### 当天数据流

```text
Customer Message → FastAPI → CustomerSupportGraph
                 → ProblemDetails → Customer Response
```

### 当天验收

- 客户可以用一句普通语言开始支持会话。
- Agent 返回结构化问题理解和简单回复。
- 回复不要求客户知道任何内部技术名词。
- 没有客户提供的事实不会被模型补写。
- 新能力真实通过 FastAPI 和 LangGraph 运行。

---

## Day 2 — 多轮澄清与信息逐步补全

### 当天目标

让客户智能体可以继续同一个会话，每次只问一个真正影响处理的问题，并把客户回答合并到已有问题资料中。

### 需要完成

1. 扩展 `CustomerSupportState`：
   - 有限消息列表。
   - 已经提出的问题。
   - 当前缺失信息。
   - 当前会话轮数。

2. 增加 `POST /api/v1/support-sessions/{session_id}/messages`，使用同一个 `thread_id` 恢复当前 Graph 状态。

3. 增加最小条件路由：
   - 信息仍然不足 → `ask_for_information`。
   - 已经足够 → `ready_for_diagnosis`。

4. `ask_for_information` 每次只生成一个问题，并满足：
   - 客户能够直接回答。
   - 没有重复询问。
   - 不要求客户查找内部系统信息。
   - 清楚说明为什么需要这个信息。

5. 新消息到达后重新生成或更新 `ProblemDetails`，但保留已经确认的事实。模型不能静默覆盖客户之前明确说出的信息。

6. 设置最多三次客户澄清。达到限制仍无法继续时，状态进入 `needs_assistance`，不无限追问。

7. 保留一条“三轮普通对话 → 问题资料补全”的关键测试，以及一条重复问题检查。

8. Day 2 明确不加入自动工具、RAG、摘要器、长期 Memory、数据库消息表或 Ticket。

### 当天数据流

```text
New Customer Message + Existing State
        ↓
Update ProblemDetails
        ↓
Missing Information?
   ├── Yes → Ask One Plain Question
   └── No  → Ready for Diagnosis
```

### 当天验收

- 相同 `session_id` 可以连续完成多轮对话。
- 客户回答会进入同一份 `ProblemDetails`。
- 每轮最多出现一个澄清问题。
- Agent 不重复询问客户已经提供的信息。
- 会话达到限制时可以停止，不会无限循环。

---

## Day 3 — 自动读取客户环境

### 当天目标

让系统优先自动读取当前客户和产品已经知道的信息，避免把版本、账户状态和最近操作继续问给客户。

### 需要完成

1. 在 ResolveLab 增加第一个客户可见场景，客户只知道“某个功能无法使用”。场景提供：
   - 当前账户简单状态。
   - 当前产品版本。
   - 相关功能是否启用。
   - 最近一次相关操作的简单结果。

2. 增加最少的客户可见 Simulator API，只返回客户有权看到的数据。内部原因、其他客户信息和内部运行记录不能出现。

3. 增加客户范围只读能力：
   - `get_current_product_context`：由固定 Graph 节点调用，读取总是需要的账户和版本信息。
   - `get_recent_customer_activity`：由 Customer Support Agent 在需要时选择调用。

4. 客户 ID 从支持会话的服务器数据绑定到工具。工具 Schema 不能让模型输入任意 `customer_id`。

5. 在 `CustomerSupportGraph` 增加 `get_customer_data`，放在向客户提问之前。

6. 路由顺序改为：

```text
Understand → Collect Available Context → Recheck Missing Information → Ask or Continue
```

7. 保留三条关键验证：
   - 自动取得版本后不再询问版本。
   - 模型不能查询另一个客户。
   - Simulator 客户接口不返回内部字段。

8. Day 3 明确不增加通用 Tool Gateway、认证后台、内部新工具、写工具或 RAG。

### 当天数据流

```text
Customer Message → Understand Problem
        ↓
Server-bound Customer Context Tools
        ↓
Updated ProblemDetails
        ↓
Only Ask What the System Cannot Know
```

### 当天验收

- Agent 会先使用系统已有信息，再决定是否询问客户。
- 客户不再被要求提供系统能够自动读取的信息。
- 客户工具无法跨客户读取数据。
- 客户工具结果只包含客户可见内容。

---

## Day 4 — 第一版客户问题检索与引用

### 当天目标

让客户智能体能够查询真实客户文档，并使用适用于当前功能和版本的内容回答客户。

### 需要完成

1. 准备第一批约 6 份简短客户文档：
   - 功能基础说明。
   - 常见问题。
   - 简单排查步骤。
   - 版本差异。
   - 客户能够执行的恢复方法。

2. 为每份文档保存最小元数据：
   - `visibility=CUSTOMER`。
   - feature。
   - version。
   - source URI。
   - effective dates。

3. 增加 `Document`、`DocumentChunk` 和 Alembic Migration。启用 pgvector，并使用 384 维向量匹配默认本地 Embedding 模型。

4. 建立同步摄取命令：
   - 读取仓库文档。
   - 简单清洗和 Chunking。
   - 生成 Embedding。
   - 保存或更新 Chunk。

5. 实现第一版语义检索，返回少量：
   - chunk ID。
   - source URI。
   - version。
   - content。
   - score。

6. 包装 `retrieve_documents_for_customer_question` 只读工具。数据库查询必须先过滤 `visibility=CUSTOMER`，不能依靠模型隐藏内部资料。

7. 把检索加入 `CustomerSupportGraph`。只发送 Top-K 少量片段，并要求客户建议引用真实 chunk ID。

8. 使用约 5 条真实查询检查结果，并保留一条“恶意文档内容不能改变工具权限”的测试。

9. Day 4 明确不加入内部文档检索、全文检索、混合检索、Reranker、文档管理 API 或后台 Worker。

### 当天数据流

```text
ProblemDetails + Customer-side Data
        ↓
retrieve_documents_for_customer_question
        ↓
Customer-visible Chunks
        ↓
Grounded Customer Response + Citation IDs
```

### 当天验收

- Customer Support Agent 的回答可以追溯到客户可见文档。
- 旧版本文档不会覆盖当前版本。
- 内部资料不能出现在客户检索结果中。
- 文档内容不能改变系统规则或工具权限。
- RAG 真实进入 Customer Support Graph，而不是独立脚本。

---

## Day 5 — 客户侧解决、确认与最小聊天界面

### 当天目标

完成第一条“客户描述 → 自动收集 → 知识检索 → 简单解决 → 客户确认”的完整路径，并通过 Vite 页面演示。

### 需要完成

1. 定义最小 `CustomerResolution`：
   - 简单原因说明。
   - 最多三个客户步骤。
   - citation IDs。
   - verification method。

2. 在 Customer Support Graph 增加必要能力：
   - `diagnose_customer_issue`。
   - `offer_customer_resolution`。
   - `wait_for_customer_verification`。
   - `finalize_customer_resolution`。

3. 客户完成建议后，可以通过下一条消息确认结果。系统保存验证来源：
   - 客户明确确认；或
   - 客户工具重新读取到状态变化。

4. 如果客户确认没有解决，状态进入 `unresolved`。今天只记录这个状态，不创建 Ticket。

5. 完成第一个客户可自行解决场景：
   - 客户使用普通语言描述功能不能使用。
   - 系统自动读取相关设置。
   - Customer RAG 返回适用指导。
   - 客户完成步骤并确认恢复。

6. 在 Vite 前端增加最小 Customer View：
   - 消息列表。
   - 输入框和发送按钮。
   - 当前问题摘要。
   - 自动收集的信息。
   - 客户可见引用。
   - 当前状态。

7. 前端直接使用 `fetch()` 调用现有 API。使用组件本地状态，不增加 Router、Context 或状态管理库。

8. 保留一条完整后端测试和一次浏览器主路径检查。

9. Day 5 明确不创建 Ticket、Support View、内部新工具、SSE 或复杂聊天组件库。

### 当天数据流

```text
Customer Chat UI → CustomerSupportGraph
        ↓
Context Tools + Customer RAG
        ↓
Plain Resolution Steps
        ↓
Customer or Tool Verification
        ↓
Resolved
```

### 当天验收

- 客户可以在浏览器中完成一条简单问题解决路径。
- 建议使用普通语言并包含有效客户引用。
- 系统记录问题是否真实恢复，而不是在给出建议后立即结束。
- 该场景不创建 Ticket。
- 页面只展示客户有权查看的信息。

---

## Day 6 — 无法解决时自动创建高质量工单

### 当天目标

把客户侧无法解决的问题转换成结构化调查资料，并自动创建 Ticket。Ticket 从今天开始成为客户处理的结果，而不是客户开始求助的入口。

### 需要完成

1. 定义 `SupportHandoff`，只包含：
   - support session ID。
   - 服务器确认的 customer ID。
   - 问题总结。
   - 受影响功能。
   - 客户影响。
   - 时间和环境。
   - 自动收集的事实和来源。
   - 已经尝试的步骤。
   - 客户引用 ID。
   - 未解决原因和剩余问题。

2. 增加 `build_support_handoff`。模型整理自然语言内容，普通代码加入客户 ID、来源引用和会话 ID。

3. 增加 `support_sessions` 表和 Migration，只保存：
   - session ID。
   - customer ID。
   - thread ID。
   - status。
   - final problem details。
   - created/updated time。

4. 扩展现有 `tickets`，让 Ticket 保存唯一的 `support_session_id` 和完整 `SupportHandoff`。Ticket 是交接内容的唯一保存位置；Support Session 不反向保存 Ticket ID 或 handoff。自动建单不再调用旧分类模型，旧工单字段只用于兼容历史数据。

5. Customer Support Graph 在以下情况进入 handoff：
   - 客户确认建议无效。
   - 客户范围内没有足够信息。
   - 问题明显需要内部调查。
   - 客户澄清达到限制。

6. 创建 Ticket 必须幂等：同一个 support session 只能生成一个 Ticket。重复请求返回同一个 ticket ID。

7. 由自动工单路径替代手动分类和手动创建 Ticket 的正式入口。测试需要造 Ticket 时使用 Fixture 或明确的开发辅助函数，不保留两套正式入口。

8. Customer API 返回 Ticket ID 和“已经交给技术支持继续调查”的普通语言状态。

9. 保留一条“客户建议无效 → handoff → Ticket → 重新读取”的关键测试和一条重复提交测试。

10. Day 6 明确不启动 Support Graph、不增加 Ticket Queue、消息表、审计时间线或通用状态机。

### 当天数据流

```text
Unresolved Customer Session
        ↓
SupportHandoff
        ↓
Idempotent Ticket Creation
        ↓
Ticket ID + Customer-facing Status
```

### 当天验收

- 未解决客户会话可以自动创建结构化 Ticket。
- Ticket 包含客服继续调查需要的信息，而不是只包含聊天文本。
- 已收集信息和已尝试步骤不会在交接中丢失。
- 相同会话不会重复创建 Ticket。
- 客户不能在 handoff 中伪造另一个 customer ID。

---

## Day 7 — 客户到客服端调查的第一条完整链路

### 当天目标

让自动创建的 Ticket 进入客服端调查智能体，使用现有内部只读工具得到有依据的结果，并把客户可见结论返回同一支持会话。

### 需要完成

1. 更新 `SupportCaseState` 和 `TicketContext`，输入以 `SupportHandoff` 为主，不再假设 Ticket 由客服手动填写。

2. 更新客服端 System Prompt：
   - 客户侧已经收集的事实不能无理由重复查询或重新询问。
   - 使用内部工具前先读取 handoff。
   - 每个关键结论必须来自工具事实。
   - 没有足够证据时升级，不猜测。

3. 让现有 `SupportInvestigationGraph` 接收自动 Ticket：
   - `load_handoff`。
   - `investigate`。
   - `finalize_support_result`。

4. Ticket 创建完成后，由普通后端代码启动客服端调查。今天可以同步运行，不提前加入任务队列。

5. 定义最小 `SupportInvestigationResult`：
   - conclusion。
   - supporting facts。
   - customer explanation。
   - outcome：`resolution` 或 `engineer_escalation`。

6. 将客户可见结果保存到 Ticket 和 support session。内部原始结果不直接返回 Customer View。

7. 在现有 Vite 应用增加最小 Support View：
   - 结构化 handoff。
   - 调查使用的工具名称。
   - 关键事实。
   - 当前结论和结果。

8. Support View 通过简单切换按钮进入，不增加独立前端、认证页面或路由库。

9. 保留一条完整测试：普通客户描述 → 自动信息收集 → 自动 Ticket → Support Graph → 客户可见结果。

10. Day 7 明确不增加新场景、内部 RAG、Evidence 表、审批、异步 Worker 或 SSE。

### 当天数据流

```text
Customer Session → SupportHandoff → Ticket
        ↓
SupportInvestigationGraph → Internal Read-only Tools
        ↓
Support Result → Customer-safe Explanation
```

### 当天验收

- 一个客户问题可以完整经过两个 Graph。
- 两个 Graph 只通过结构化 handoff 交换信息。
- Support Agent 不会重新询问已经由客户侧确认的信息。
- 客户只能看到安全、简单的最终说明。
- Support View 能展示调查事实，但不显示隐藏思维过程。

---

## Day 8 — 扩展代表场景和按需工具

### 当天目标

证明完整流程不是针对一个问题写死，并继续按场景需要扩展客户工具、内部工具和 ResolveLab。

### 需要完成

1. 建立版本化场景 JSON，至少覆盖四条主路径：
   - 客户可以直接解决。
   - 客户无法解决，但客服端可以给出解决方案。
   - 客服端确认需要一个安全内部操作。
   - 两层智能体都无法确认，必须交给工程师。

2. 每个场景保存：
   - 客户会说出的普通描述。
   - 系统能够自动取得的信息。
   - 真实原因。
   - 预期客户工具和内部工具。
   - 禁止工具。
   - 预期结果。

3. 只增加这些场景真正需要的客户能力，例如：
   - 当前功能设置。
   - 最近操作状态。
   - 客户可见系统提示。

4. 只增加这些场景真正需要的内部工具，例如：
   - 查询后台操作详情。
   - 查询账户权限。
   - 查询受限时间范围内的运行记录。
   - 查询平台状态。

5. 每增加一个场景就立即走通：

```text
Customer Message → Customer Support Graph → Handoff → Ticket → Support Graph → Outcome
```

6. 工具错误作为结构化失败返回。工具不可用且没有替代证据时，Agent 必须升级，不能编造结果。

7. 如果三个以上工具出现完全相同的 HTTP 读取代码，只抽取一个普通 `get_resolvelab_data()` 辅助函数；不创建 Client Class 或 Gateway。

8. 评估输入累计到约 12 条，覆盖模糊描述、信息缺失、相似现象和不同结果。今天不建立 Runner。

9. Day 8 明确不增加工具重试框架、通用场景引擎、并发调查、多个写操作或完整错误类型体系。

### 当天数据流

```text
Versioned Scenario → Customer-visible State + Internal State
        ↓
Customer Tools / Support Tools
        ↓
Customer Support Graph → Handoff → Support Graph → Expected Outcome
```

### 当天验收

- 四条主路径都存在可重复的 ResolveLab 场景。
- Customer Support Agent 仍然只能访问客户工具。
- Support Agent 仍然只能访问内部只读工具。
- 相似客户描述可以根据真实状态进入不同结果。
- 工具失败时系统能够安全升级。

---

## Day 9 — 证据驱动诊断和工程师升级包

### 当天目标

让客服端调查结果从普通事实列表升级为可验证的证据诊断，并在无法解决时生成工程师可以直接继续处理的升级包。

### 需要完成

1. 定义最小 `EvidenceItem`：
   - evidence ID。
   - source type。
   - source reference。
   - observed at。
   - summary。
   - customer visibility。

2. Evidence ID 由普通代码根据真实工具结果创建，模型只能引用已经存在的 ID，不能自己生成不存在的证据。

3. 扩展 `SupportInvestigationResult`：
   - root cause。
   - supporting evidence IDs。
   - contradicting evidence IDs。
   - confidence band：low / medium / high。
   - resolution。
   - escalation reason。

4. 增加一个普通验证函数：
   - 引用的 evidence ID 必须真实存在。
   - 证据必须属于当前客户和 Ticket。
   - 证据时间必须与当前问题相关。
   - 没有足够支持证据时不能输出确定结论。

5. 定义 `EngineerEscalationPackage`：
   - 问题和客户影响。
   - 客户侧诊断和尝试。
   - 内部证据。
   - 已排除原因。
   - 当前可能原因。
   - 未回答问题。
   - 建议工程师下一步检查方向。

6. 只有页面或 API 需要在 Checkpoint 之外查询 Evidence 时才增加 `evidence_items` 表；否则先把结构化 Evidence 保存在 Ticket 调查结果中。

7. 在 Support View 展示 Evidence ID、来源摘要、诊断和工程师升级包，不展示原始无限日志。

8. 保留三条关键测试：有效证据诊断、虚假 evidence ID 被拒绝、冲突证据触发升级。

9. Day 9 明确不加入内部 RAG、Evidence Service、Validator 类层级、证据 Hash 或复杂去重系统。

### 当天数据流

```text
Internal Tool Results → Evidence Items
        ↓
Evidence Validation
        ↓
Supported Diagnosis
   ├── Enough Evidence → Resolution
   └── Insufficient / Conflict → Engineer Escalation Package
```

### 当天验收

- 每个主要内部结论都引用真实 Evidence ID。
- 不存在的证据不能进入最终结果。
- 证据不足或冲突时系统自动升级。
- 工程师升级包包含客户和客服两层已经完成的调查。
- 客户界面不会泄露内部证据原文。

---

## Day 10 — 内部知识检索、版本和引用验证

### 当天目标

让客服端调查智能体可以查询内部支持知识，并确保客户资料和内部资料在数据库、模型上下文和最终输出中保持隔离。

### 需要完成

1. 文档总量扩展到至少 15 份：
   - 约 8 份客户文档。
   - 约 7 份内部文档。
   - 包含一个过期版本和一个带恶意指令的测试文档。

2. 复用现有 `documents` 和 `document_chunks`，通过 `visibility` 区分 `CUSTOMER` 和 `INTERNAL`，不创建两套表。

3. 实现 `search_internal_knowledge` 只读工具：
   - 查询阶段固定过滤 `visibility=INTERNAL`。
   - 返回 chunk ID、source URI、version、content 和 score。
   - 只绑定到 Support Agent。

4. 扩展引用校验：
   - 文档和 Chunk 必须存在。
   - 文档可见范围必须匹配当前 Agent。
   - 文档版本当前有效。
   - 引用内容支持对应结论。

5. 使用约 10 条查询分别检查客户和内部检索。只有明确名称、版本或错误标识确实经常漏检时，才增加 PostgreSQL Full-Text Search 和一个简单结果合并函数。

6. Customer Support Agent 的检索测试必须证明内部文档永远不会返回。Support Agent 的客户回复生成步骤必须过滤内部原文。

7. 把内部知识引用加入 Evidence Validation 和 Support Result。

8. 保留一条完整测试：Customer RAG → Handoff → Internal RAG + Tools → Evidence-based Result。

9. Day 10 明确不增加独立向量数据库、Reranker、Document API、后台摄取 Worker、Retriever 基类或第二套摄取流程。

### 当天数据流

```text
Customer Problem → Customer Knowledge Filter → Customer Citations

Ticket + Internal Evidence → Internal Knowledge Filter
        ↓
Citation Validation → Supported Internal Result → Customer-safe Explanation
```

### 当天验收

- 两个 Agent 分别只能检索自己的知识范围。
- 当前版本文档优先，过期文档不能覆盖它。
- 恶意文档不能改变工具权限、Graph 路由或系统规则。
- 内部文档结论带有效引用，但内部文本不会返回客户。
- 是否加入全文检索由真实查询结果决定。

---

## Day 11 — PostgreSQL Checkpoint 和完整恢复

### 当天目标

让客户多轮会话、客服端调查和后续人工等待可以在 API 重启后恢复，不重复创建 Ticket 或重复执行已经完成的步骤。

### 需要完成

1. 增加 `langgraph-checkpoint-postgres`，使用与当前同步或异步调用方式匹配的 PostgreSQL Saver。不为了使用 Checkpointer 重写整个 FastAPI 调用模型。

2. 将两个 Graph 的 `InMemorySaver` 替换为 PostgreSQL Checkpointer。

3. 使用稳定 thread ID：
   - customer thread ID 来自 support session。
   - support thread ID 来自 Ticket 或独立 support run ID。

4. 保证业务副作用可重复恢复：
   - Support Session 创建一次。
   - 同一会话只创建一个 Ticket。
   - 已完成工具步骤不会因为 API 重启而无理由重复。

5. `support_sessions` 保存客户 thread ID、当前状态和客户可见结果。关联 Ticket 通过 `tickets.support_session_id` 查询，避免双方重复保存编号。只有查询 Support Run 的需求已经真实存在时，才增加最小 `agent_runs` 表。

6. 增加 `GET /api/v1/support-sessions/{session_id}`，让页面刷新后恢复消息、问题摘要、引用、Ticket 和处理状态。

7. 增加最小 `GET /api/v1/tickets/{ticket_id}/investigation`，只返回 Support View 当前需要的结构化结果。

8. 使用新 Graph 实例模拟服务重启，验证：
   - 客户可以继续原会话。
   - Support Graph 可以继续原调查。
   - Ticket 不重复创建。
   - 已完成副作用不重复执行。

9. Day 11 明确不创建长期用户 Memory、聊天向量库、Checkpoint Repository、完整状态时间线或恢复管理后台。

### 当天数据流

```text
Support Session ID / Ticket ID → Stable Thread ID
        ↓
PostgreSQL Checkpointer → Saved Graph State
        ↓
API Restart → Resume Same State → Continue Without Duplicate Side Effects
```

### 当天验收

- API 重启后客户可以继续同一支持会话。
- API 重启后 Support Graph 可以恢复当前 Ticket 调查。
- 页面刷新不会丢失客户可见状态。
- 恢复不会重复创建 Ticket 或重复执行已完成副作用。
- Checkpoint 与 Ticket 等业务数据边界明确。

---

## Day 12 — 动作提议、策略检查和幂等边界

### 当天目标

让客服端调查智能体可以根据证据提出一个安全内部操作，但今天只能停在“允许、拒绝或等待审批”，Agent 不能执行写操作。

### 需要完成

1. 只选择一个完整场景和一个安全操作：
   - `retry_failed_operation`。

2. 定义最小 `ActionProposal`：
   - action name。
   - reason。
   - supporting evidence IDs。
   - intended target reference。
   - expected result。
   - verification method。

3. Agent 输出只包含动作意图和证据。可执行参数由后端从当前 Ticket、Evidence 和 ResolveLab 可信数据重新构造。

4. 使用一个普通 Python 函数实现 Policy Gate：
   - 只允许白名单动作。
   - 要求有效支持证据。
   - 目标必须属于当前 Ticket 客户。
   - 当前状态必须允许重试。
   - 参数通过 Pydantic 校验。
   - 始终要求人工审批。

5. 在 ResolveLab 增加一个带 idempotency key 的写 API。相同 key 重复调用不能再次改变状态。

6. Support Graph 增加提议和策略路由，但不能调用 ResolveLab 写 API：

```text
Diagnosis → Action Proposal → Policy Gate
                         ├── Rejected
                         └── Awaiting Approval
```

7. 保留关键安全测试：
   - 两个 Agent 的工具列表都没有写工具。
   - 缺少证据的提议被拒绝。
   - 错误客户目标被拒绝。
   - 未审批执行次数为零。
   - ResolveLab 相同 idempotency key 只改变一次状态。

8. Day 12 明确不创建通用 Policy Engine、角色管理系统、风险矩阵、多个写动作或审批 Graph。

### 当天数据流

```text
Evidence-based Diagnosis → ActionProposal
        ↓
Deterministic Policy Gate
   ├── Rejected
   └── Awaiting Human Approval
```

### 当天验收

- Support Agent 可以提出有证据的安全操作。
- Agent 自己不能执行任何写操作。
- Policy Gate 完全由普通 Python 代码决定。
- 未审批动作执行次数为零。
- ResolveLab 写 API 具备真实幂等行为。

---

## Day 13 — 人工审批、执行和结果验证

### 当天目标

完成“提议 → 暂停 → 人工决定 → 恢复 → 执行 → 重新验证 → 返回客户或升级工程师”的闭环。

### 需要完成

1. 增加最小数据库表：
   - `action_proposals`。
   - `approvals`。
   - `action_executions`。

2. 在 Support Graph 中使用 LangGraph `interrupt()` 建立 `wait_for_approval`。中断载荷只包含审批页面需要的结构化信息。

3. 增加审批 API，支持：
   - Approve。
   - Reject。

4. Resume 时重新检查：
   - Approval 是否属于当前 Proposal。
   - 当前用户是否为演示审批角色。
   - Proposal 和目标状态是否仍然有效。
   - Policy Gate。
   - idempotency key。

5. `execute_action` 使用普通 Python 函数调用 ResolveLab 写 API，保存执行前状态、执行后返回、external reference 和错误。

6. `verify_action` 重新调用只读工具获取当前状态。写 API 返回成功不能直接视为问题已解决。

7. 验证成功时更新 Ticket 和 support session，并向客户生成简单说明。验证失败时生成 Engineer Escalation Package。

8. 在 Support View 增加一个简单 Approval Card，并在 Customer View 显示“正在等待技术人员确认”的状态。

9. 保留关键测试：Approve、Reject、等待审批期间重启、重复 Resume、验证失败和未审批绕过。

10. Day 13 明确不增加 Edit 决策、审批工作流框架、多个审批层级、多个写动作或完整权限后台。

### 当天数据流

```text
Action Proposal → Policy Gate → LangGraph Interrupt
        ↓
Human Approve / Reject
        ↓
Resume → Recheck → Execute Once → Read-only Verification
        ↓
Resolved Customer Response / Engineer Escalation
```

### 当天验收

- 未审批动作执行次数为零。
- 等待审批期间重启后仍可继续。
- 同一个 idempotency key 只执行一次。
- Reject 不会调用写 API。
- 系统根据重新读取的状态决定解决或升级。
- 客户可以看到处理状态和最终结果，但看不到内部敏感数据。

---

## Day 14 — 智能体评估和安全回归

### 当天目标

用可重复数据证明系统减少了无效提问、提高了工单质量，并且两个智能体没有越过知识、工具和审批边界。

### 需要完成

1. 完成至少 24 个版本化 JSON 评估案例：
   - 客户普通表达和不同措辞。
   - 信息完整和缺失。
   - 系统可以自动获取的信息。
   - 客户直接解决。
   - 自动 Ticket 和客服端解决。
   - 安全操作和人工审批。
   - 工程师升级。
   - 工具失败、证据冲突、恶意客户消息和恶意文档。

2. 每个案例保存：
   - customer message。
   - scenario ID。
   - expected collected fields。
   - expected and forbidden questions。
   - expected customer tools and support tools。
   - required evidence or citations。
   - expected handoff fields。
   - expected final outcome。

3. 建立一个简单命令行 Runner，逐条调用真实 Customer Support Graph 和需要时的 Support Graph，输出版本化 JSON 报告。不增加评估 API、队列或数据库表。

4. 实现普通代码评估器：
   - ProblemDetails Schema validity。
   - 问题理解准确性。
   - 不必要问题数量。
   - 系统已知信息重复询问率。
   - Customer citation validity。
   - Handoff completeness。
   - Required / forbidden tool usage。
   - Evidence coverage。
   - Diagnosis and escalation decision。
   - Approval bypass 和 idempotency。

5. 记录每个案例的：
   - 两个 Graph 的路径。
   - 工具轨迹。
   - 引用和证据。
   - 最终结果。
   - latency。
   - 模型调用次数。
   - Token 和成本。

6. 使用 LangSmith Dataset 和 Experiment 运行正式实验。没有 Key 时，本地 Runner 仍必须产生完整报告。

7. 在代表性案例上比较一次性回答和完整 ResolveAI 流程，重点比较：
   - 是否重复询问信息。
   - 是否有真实依据。
   - 工单是否完整。
   - 是否正确升级。

8. 运行安全回归：
   - 客户请求内部数据。
   - 客户试图指定其他 customer ID。
   - 客户文档包含恶意指令。
   - Support Agent 尝试引用不存在证据。
   - 未审批动作和重复执行。

9. 失败案例必须进入固定数据集。不得删除失败样本或伪造目标指标。

10. Day 14 明确不建立 Dashboard、Evaluator 类体系、LLM-only 安全评分、完整故障组合矩阵或模型微调流程。

### 当天数据流

```text
Versioned Evaluation Cases → Customer Support Graph → Optional Support Graph
        ↓
Local Evaluators + LangSmith Experiment
        ↓
Metrics, Traces, Costs and Failure Report
```

### 当天验收

- 至少 24 个案例可以重复运行。
- 系统已知信息重复询问率有真实结果。
- Forbidden Tool Call 和 Approval Bypass 为零。
- 每个失败可以追溯到输入、Graph、工具、引用、证据和结果。
- 实际指标、延迟和成本已经保存。
- LangSmith 或本地报告可以支持作品集中的真实结论。

---

## Day 15 — 完整验收、交付和作品集收尾

### 当天目标

只修复阻塞完整流程的问题，并让新环境可以运行和演示 ResolveAI V2。今天不增加新的 Agent 能力。

### 需要完成

1. 运行完整验收：
   - 后端和 Simulator 测试。
   - Ruff。
   - 前端 ESLint 和 Build。
   - 24 个案例完整评估。
   - 四条浏览器演示路径。
   - Checkpoint 重启恢复。
   - 审批和幂等验证。

2. 完善最小前端：
   - Customer View 可以完成对话、查看自动信息、引用、建议和 Ticket 状态。
   - Support View 可以查看 handoff、证据、诊断、审批和升级包。
   - 页面刷新后从后端恢复状态。
   - 不显示密钥、隐藏思维过程或无限原始日志。

3. 增加 Backend、Simulator 和 Frontend Dockerfile，完善 Docker Compose：
   - PostgreSQL + pgvector。
   - backend。
   - simulator。
   - frontend。
   - health checks。
   - 明确 migration 和文档摄取命令。

4. 完善 GitHub Actions：
   - Ruff。
   - 后端和 Simulator 测试。
   - PostgreSQL migration check。
   - ESLint 和 TypeScript Build。
   - 低成本 Agent smoke evaluation。
   - 必要 Docker build。

5. README 不作为 Day 15 的默认交付项。只有用户明确要求时，才集中更新根目录或指定目录 README，并且只写最终项目能力，不写开发进度。

6. 完成作品集材料：
   - 系统架构图。
   - 两个 LangGraph 状态图。
   - 简化数据关系图。
   - 客户解决、自动工单、审批和人工升级截图。
   - 3–5 分钟演示视频脚本或视频。
   - Threat Model 和关键架构决定。

7. 只修复：
   - 数据丢失。
   - 无法恢复。
   - 客户或知识越权。
   - 错误引用或证据。
   - 审批绕过。
   - 重复写操作。
   - 主要流程失败。
   - 安装和启动阻塞。

8. 公开部署只在当前账户和时间允许时执行。无法部署时记录限制，不用新的云平台工程替代 Agent 收尾。

9. 完成最终 Git 检查，确保没有密钥、缓存、日志、本地数据库数据、模型下载文件或计划内未提交源码。

10. Day 15 明确不增加新工具、新场景、新数据表、新页面、实时通信、基础设施平台或模型供应商。

### 当天数据流

```text
Fresh Local Start → Customer View → Customer Support Graph → Ticket
        ↓
Support Graph → Resolution / Approval / Engineer Escalation
        ↓
Repeatable Evaluation + Portfolio Evidence
```

### 当天验收

- 一条明确命令可以启动完整本地应用。
- 客户直接解决、自动工单、审批操作和工程师升级四条路径都能演示。
- API 重启后关键状态不丢失。
- 24 个评估案例和实际指标可以复现。
- 两个 Agent 的工具和知识权限保持隔离。
- 作品集只描述真实实现；README 只有在用户明确要求时才更新。
- 新开发者可以根据文档启动并理解完整数据流。

---

## 6. 累积里程碑

| 时间点 | 必须已经具备的能力 |
|---|---|
| Day 0 | 现有客服端调查基础完成重新定位，前端迁移到 Vite |
| Day 1 | 客户一句普通描述可以进入 Customer Support Graph |
| Day 2 | 客户可以多轮补全问题，Agent 不重复提问 |
| Day 3 | 系统自动读取已有信息，不再询问客户 |
| Day 4 | 客户问题检索和有效引用进入 Customer Support Graph |
| Day 5 | 客户可以在浏览器中完成一条直接解决路径 |
| Day 6 | 未解决会话可以自动生成高质量 Ticket |
| Day 7 | Ticket 可以自动进入 Support Graph 并返回结果 |
| Day 8 | 四条代表路径和按需工具可以运行 |
| Day 9 | 内部诊断有真实 Evidence，并能生成工程师升级包 |
| Day 10 | 内部知识检索、版本和可见范围验证完成 |
| Day 11 | 两个 Graph 可以使用 PostgreSQL Checkpoint 恢复 |
| Day 12 | Agent 可以提出动作，但未审批执行次数为零 |
| Day 13 | HITL、幂等执行和执行后验证形成闭环 |
| Day 14 | 24 个案例产生真实智能体和安全指标 |
| Day 15 | 完整本地应用、CI、文档和作品集材料完成 |

---

## 7. 每日统一退出条件

当天结束前必须回答：

1. 今天新增的能力是否进入真实 Customer Support Graph、Support Graph 或两者之间的交接？
2. Agent 是否减少了客户需要自己寻找的信息，而不是增加问题数量？
3. 客户看到的语言是否不要求技术背景？
4. 两个 Agent 的工具和知识范围是否仍然隔离？
5. 每个主要建议或诊断是否有客户引用、工具事实或内部 Evidence？
6. 当天出现的新副作用是否具备权限检查、幂等或关键测试？
7. 模型上下文是否只包含当前步骤需要的有限数据？
8. 是否没有保存或展示隐藏思维过程？
9. 是否没有增加当天主路径不使用的类、表、工具、节点、依赖或页面？
10. 正常路径、失败路径和当天安全边界是否已经验证？
11. Ruff、相关 Pytest 以及发生变化时的前端检查是否通过？
12. 下一天能否直接在今天的真实能力上继续？

任何一项为“否”，先修复当天内容，不开始下一天。

---

## 8. 15 天内明确不做

- 脱离项目的课程式练习和学习目录。
- 自由协作的多智能体集群、Agent Swarm、Supervisor Agent 或动态角色系统。
- 让客户理解技术术语后才能完成的问卷式客服。
- 只聊天、不自动收集信息、不生成交接资料的普通 Chatbot。
- 两个独立前端、完整客服后台或复杂管理系统。
- 复杂认证平台、企业 SSO 和用户管理后台。
- 远程控制客户设备。
- 任意 SQL、Shell、文件路径或 URL 工具。
- 让模型决定客户身份、数据权限、审批或事务。
- 完全自主修改生产环境。
- 多模型 Provider 平台、Adapter 或模型注册系统。
- 独立向量数据库。
- 文档摄取 API、后台 Worker 和消息队列。
- 没有评估依据的 Reranker、缓存或并发优化。
- 跨 Ticket、跨客户的长期 Agent Memory 和用户画像。
- 在真实重复前创建 Client Class、Gateway、Repository、Factory、通用 Service 或自定义错误层级。
- 多个写操作、通用 Policy Engine、复杂风险矩阵和多层审批系统。
- Evaluation API、评估数据库、Dashboard 或任务队列。
- WebSocket 和默认 SSE；只有普通请求无法满足真实交互时才考虑 SSE。
- Kubernetes、SQS、完整 Terraform、多环境平台和大规模云基础设施。
- Fine-Tuning。

这些能力只有在 15 天核心流程完成，并且出现真实需求和评估证据后再考虑。
