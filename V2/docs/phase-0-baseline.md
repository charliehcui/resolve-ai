# Phase 0 基线与实施门禁

> 完成日期：2026-09-17  
> 状态：Phase 0 完成；Phase 1 等待用户确认。  
> 本文不定义新业务需求，只记录实施边界。

## 1. 事实来源

`V2/building-plan-V2.md` 是 V2 业务背景、架构、数据流、功能范围和验收标准的唯一事实来源（Source of Truth）。`V2/refactor-plan.md` 只是实施顺序。

发生冲突时：

1. 立即以 `building-plan-V2.md` 为准。
2. 修正实施计划或实现。
3. 不修改 building plan 来迁就已有代码。

Phase 0 检查时，building plan 共 496 行，SHA-256 为：

```text
F8DDC3E8354197E99942A868BE131C14CF6B9D9EF1B3F30EC8435459E71A2ABC
```

该哈希只用于证明 Phase 0 没有修改事实来源。后续用户主动更新 building plan 时，应记录新版本，不能把旧哈希当成永久规则。

## 2. V1 永久只读边界

V1 是已经完成的历史版本，继续保留在 GitHub。V2 开发期间：

- 不删除、移动、重命名或修改 `V1/`。
- 不修改 V1 代码、测试、database migration、README 或其他文档。
- 不在 V1 目录生成测试 cache、报告或新的运行文件。
- V1 只用于阅读、比较和复用实现思路。
- 如果复制少量通用代码，目标文件必须位于 `V2/`，并先去除 V1 业务语义。
- V2 的 import、运行命令、容器、数据库初始化和测试不得依赖 `V1/`。

Phase 0 开始时，Git 已显示 `V1/.gitignore` 为修改状态。这是进入本阶段前已存在的工作区状态，本阶段不修改或恢复它。Phase 0 记录时该文件 SHA-256 为：

```text
92CB1856352F200F76C00B25DCF2D4C4DFBF48D2891F2B67B980C99871737AF3
```

## 3. V2 实施原则

项目目标是 AI Application Engineer 作品集，不是大型生产电商基础设施。选择方案时依次考虑：

1. 数据流是否真实接通。
2. 安全边界是否由普通代码保证。
3. 代码是否清晰、可运行、可解释、适合演示。
4. 是否使用更少代码、更少依赖和更少抽象。

除非 building plan 明确要求或当前功能确实需要，不加入 Message Queue、Kafka、Redis、Kubernetes、Event Bus、CQRS、Event Sourcing、多数据库、多向量数据库、复杂缓存、复杂权限框架、复杂 Dependency Injection 或多层 Repository/Service/Manager/Factory。

平台、商家、仓库和 Support 使用小型 FastAPI 测试服务模拟真实边界，共享一个 PostgreSQL + pgvector 实例。代码只能直接操作本模块拥有的数据；Support Agent 通过 Tool / HTTP Contract 读取业务事实。

## 4. Agent 与知识边界

Customer Agent 负责理解、必要澄清、Query Rewrite、Customer RAG、Retrieval、Citation、Customer-safe Answer 和 Structured Handoff。它不能读取订单、发货、店铺后台、平台状态、内部日志或库存后台事实，也不注册这些工具。

Support Agent 读取 Structured Handoff，使用只读业务工具收集 Evidence，动态决定下一步，提出 Action Proposal，必要时升级 Engineer。它不重新运行 Customer RAG；本期没有 Internal RAG。

Handoff 是同一 Conversation 内从 Customer Agent 到 Support Agent 的角色转换。转换后 `conversation.active_role = SUPPORT`，后续消息继续进入 Support Agent。

Ticket 只在 Support 无法解决、达到 Budget、Evidence 不足、未知异常、需要 Engineer 或用户明确要求人工时创建。Ticket 不是两个 Agent 之间的通信方式。

## 5. 模型路由

Model Router 由普通代码根据任务类型确定，模型不能自行选择 Provider。

| Provider | 任务 |
| --- | --- |
| Groq | 普通问答、简单意图、Query Rewrite、简单 Structured Output、Customer RAG 最终回答、无复杂 Tool Calling 的轻量任务 |
| Google | Support Agent 复杂调查、Multi-step Reasoning、复杂 Tool Calling、Parallel Tool Calling、Evidence 冲突判断、复杂 Action Proposal |

模型名和 Key 只从 `.env` 读取，不写死。代码、日志、Trace、报告和 Git 中都不得出现 API Key。Reranking 按 building plan 接入 `qwen3-rerank`，再用少量 dev cases 比较 Retrieval Quality、Latency 和 Cost；不默认使用 Google 主模型作为 Reranker。

## 6. Tool Calling 边界

只有同时满足以下条件的 Tool Calls 才能并行：

- 都是 read-only。
- required inputs 已完整。
- 互相没有数据依赖。
- 不修改同一资源。
- Authorization 和 scope 已由普通代码验证。

Tool B 依赖 Tool A 返回的 ID、版本或状态时必须顺序调用。Google 模型可以提出多个 Tool Calls，但一个简单的确定性执行函数必须复核 Authorization、required inputs、dependency、scope、timeout 和 budget。不建立通用 Workflow Scheduler。

## 7. LLM 与安全代码边界

LLM 可以理解问题、判断缺失信息、选择只读工具、分析 Evidence、规划下一步、提出 Action Proposal 和生成解释。

普通代码必须决定 Authorization、Tenant/Company Scope、Tool/Action Permission、Approval 有效性与过期、对象版本变化、Idempotency、是否允许执行、执行是否成功、Verification 和 Ticket 是否可关闭。

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

目标是业务上只产生一次有效结果（Effectively-once Business Effect），不承诺严格 Exactly-once Execution。只使用 stable idempotency key、duplicate protection、execution claim/lease、request ID、receipt、reconciliation 和 read-after-write verification。

## 8. LangSmith 最小记录合同

优先使用 LangChain / LangGraph 已有 integration。真实运行逐步覆盖 Conversation → Agent/Graph Node → LLM → Retrieval/Rerank → Tool/Parallel Batch → Evidence → Proposal → Approval → Execution → Verification。

每次调用尽可能记录 provider、model、purpose、latency、token usage、可得 cost、error type、trace ID 和 run ID。不得记录 API Key、Authorization Header、Secret、其他公司敏感数据或 Hidden Chain of Thought。

## 9. Phase 执行门禁

- 严格按 Phase 0 → 10，一次只做一个 Phase。
- 每个产品 Phase 必须先接通入口 → Agent/Service → 数据 → Tool → 结果 → Verification 的当前纵向切片。
- 当前功能不需要的组件不提前实现。
- 每个 Phase 完成后立即停止，等待用户确认。
- 报告必须包含：完成内容、主要文件、真实数据流、测试结果、LangSmith Trace、使用模型及原因、已知问题、下一 Phase。
- Phase 9 先完成 Dataset、Eval Runner、Dry Run、运行次数、Token/费用/时间估算并停止；完整 Benchmark 需要用户再次明确确认。

## 10. Phase 1 唯一入口

Phase 1 只实施 building plan 的 Task 01，不提前实现 Support Agent、业务工具、订单/发货/库存、Action、Ticket、完整 API 或前端。

首条真实数据流必须是：

```text
终端问题
  → 身份映射
  → 问题向量
  → PostgreSQL + pgvector 资料片段
  → Groq 真实模型
  → 回答与引用
  → 保存会话、消息和调用用量
  → 终端显示
```

Phase 1 还要通过 doctor 验证 Groq/Google 配置能力、1024 维向量、LangSmith Trace 和 Secret 遮盖，但不能借能力检查提前实现后续 Agent 或 Tool。

## 11. Phase 0 验证结果

- building plan：未修改，哈希已记录。
- V1：未修改；保留进入本阶段前已有的 `V1/.gitignore` 工作区状态。
- 产品代码：未创建。
- database migration：未创建。
- 测试：未运行；Phase 0 没有运行时代码，执行的是文档、哈希和 Git 状态检查。
- LangSmith Trace：未生成；Phase 0 没有 Agent、LLM、RAG 或 Tool 调用。
- 模型调用：未执行；Phase 1 才开始真实模型验证。
- 环境配置：`V2/.env` 当前不存在。V2 不能读取 `V1/.env` 作为运行依赖；Phase 1 开始前需要由进程环境或用户准备的 `V2/.env` 提供已配置变量。不得由工具读取、打印或复制 Secret 值到日志/报告。
- 下一步：等待用户确认后执行 Phase 1 / Task 01。
