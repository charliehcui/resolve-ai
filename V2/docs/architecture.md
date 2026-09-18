# ResolveAI V2 架构

`building-plan-V2.md` 是业务和架构唯一事实来源。本文件只描述已实现结构，不新增需求。

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

平台、商家、仓库和 Support 共用一个 PostgreSQL 实例，但普通业务代码只访问自己拥有的 schema。Support 通过 HTTP Tool Contract 读取业务事实，不跨域直接修改业务表。

## 2. Agent 边界

- Customer Agent 只处理澄清、查询改写、Customer RAG、引用回答和 Structured Handoff。它没有订单、连接、发货或库存工具。
- Support Agent 读取 Handoff 和 Evidence，动态选择只读工具。Handoff 后同一会话保持 SUPPORT 角色。
- Engineer Ticket 只用于人工接手，不负责 Customer → Support 通信。
- Internal RAG、退款、退货、多仓和库存自动修复不在本期范围。

## 3. 状态修改边界

所有订单和发货写操作沿用同一顺序：

```text
Action Proposal
  → Policy Check
  → Human Approval
  → Scope / Version Recheck
  → Execute
  → Receipt / Reconciliation
  → Deterministic Verification
```

LLM 可以解释问题、选择只读工具和提出建议，但不能决定 Authorization、Approval、Idempotency、写入许可、执行成功或 Ticket Closure。

系统目标是 Effectively-once Business Effect。稳定 request ID、唯一约束、claim/lease、receipt/reconciliation 和 read-after-write Verification 共同防止重复订单或重复出库；不宣称严格 Exactly-once Execution。

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
- V2 的 import、容器构建、数据库迁移和启动命令不读取或依赖 `V1/`。

## 6. 当前测试策略

本地开发使用 deterministic mock/stub、真实 PostgreSQL、FastAPI/HTTP、Worker、Docker 和固定浏览器测试。Phase 9 Dataset/Eval Runner 保留，但付费 Benchmark、Holdout 重复运行、大量真实模型调用和远端 LangSmith Trace 当前不执行。

