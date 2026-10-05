# ResolveAI 架构

本文件描述当前已实现结构；实际行为以代码和工具契约为准。开发背景保留在 [历史计划](history/building-plan-V2.md)。

## 1. 运行组件

```text
CLI / React
    → FastAPI /api/v1
    → conversations.process_message
        ├─ Customer Agent → Customer RAG
        └─ Structured Handoff → Support Agent → Read-only Tools
                                      ├─ Evidence
                                      ├─ Action → Approval → Execution → Verification
                                      └─ Engineer Ticket → Recheck → Close or Keep Open

Platform API ←HTTP→ Merchant API + Worker ←HTTP→ Warehouse API
                         ↓
              One PostgreSQL + pgvector instance
```

平台、商家、仓库和 Support 共用一个 PostgreSQL 实例。Support 通过 HTTP Tool Contract 读取业务事实，不跨域直接修改业务表。商家的库存刷新和任务重试接口使用共用数据库原子核对来源快照后排队，模拟器（Simulator）因此能阻止读取与写入之间的来源变化；业务发布继续由现有 Worker 完成。

## 2. Agent 边界

- Customer Agent 只处理澄清、查询改写、Customer RAG、引用回答和 Structured Handoff。它没有订单、连接、发货或库存工具。
- Support Agent 读取 Handoff 和 Evidence，动态选择只读工具。Handoff 后同一会话保持 SUPPORT 角色。
- Engineer Ticket 只用于人工接手，不负责 Customer → Support 通信。
- Internal RAG、退款、退货和多仓不在本期范围。库存支持按可信现有规则刷新发布值，已有订单任务支持安全重试；这些操作需要会话所属用户明确确认。

## 3. 状态修改边界

订单、发货、库存和任务修复沿用同一顺序：

```text
Diagnosis + optional Candidate Action (same final LLM response)
  → Action Plan
  → Policy Check
  → Scoped User Confirmation (LOW) / Admin Approval (MEDIUM or HIGH)
  → Scope / Version Recheck
  → Execute
  → Receipt / Reconciliation
  → Deterministic Verification
```

LLM 可以解释问题、选择只读工具和提出建议，但不能决定 Authorization、Approval、Idempotency、写入许可、执行成功或 Ticket Closure。

系统目标是 Effectively-once Business Effect。稳定 request ID、唯一约束、claim/lease、receipt/reconciliation 和 read-after-write Verification 共同防止重复订单或重复出库；不宣称严格 Exactly-once Execution。

候选动作（Candidate Action）只包含类型、理由和证据编号（Evidence ID），不能指定风险或审批规则。动作注册表（Action Registry）仅包含可演示的四种写操作，以及无后台执行器（Executor）的重新授权要求。修改店铺全局同步开关时，风险提升为中等并要求管理员。授权、限流、外部故障和人工处理路径见 [模拟器演示](agent-simulator.md)。

## 4. Ticket 复查

被授权工程师调用同一 Ticket 的复查入口：

```text
Ticket business_target
  ├─ order → GetOrder + GetProcessRecords → existing order checks
  ├─ shipment → GetShipment + GetShipmentRecords + GetPlatformShipment → existing shipment checks
  ├─ stock → GetStockFacts → existing stock formula/version/time checks
  └─ incomplete/general → NEEDS_INFO
```

每次复查写入 `support.ticket_rechecks`，并把新 Evidence 关联到原 Support case：

- `RESOLVED`：确定性检查全部通过，Ticket 状态变为 `closed`。
- `UNRESOLVED`：业务结果未满足，Ticket 保持或重新变为 `open`。
- `NEEDS_INFO`：缺少业务标识或来源事实，Ticket 保持 `open`。

关闭后的 Ticket 仍可通过原授权读取和导出。复查失败会重新打开 Ticket，不能只依赖历史成功状态。

## 5. 部署与隔离

- `Dockerfile`：Platform、Merchant、Warehouse 和 Worker 的最小业务镜像，不包含模型依赖。
- `Dockerfile.api`：Support API 镜像，包含 `app/`、migration 和 prompt；不包含 Lab、Eval 或 Holdout。
- `Dockerfile.init`：一次性执行 migration 和本地演示 bootstrap；只有它可写 `.local` token 文件。
- 业务服务和 API 只读挂载 `.local`；`LANGSMITH_TRACING=false` 是默认值。
- 当前代码、容器构建、数据库迁移和启动命令均使用仓库根目录。

## 6. 当前测试策略

本地开发使用 deterministic mock/stub、真实 PostgreSQL、FastAPI/HTTP、Worker、Docker 和固定浏览器测试。Phase 9 Dataset/Eval Runner 保留，但付费 Benchmark、Holdout 重复运行、大量真实模型调用和远端 LangSmith Trace 当前不执行。
