# Phase 6 / Task 07–10 完成报告

> 保存日期：2026-09-18  
> 状态：**Complete / External Acceptance Deferred**。本地实现、真实业务数据流、安全回归和恢复验收已通过；三条真实 LLM Shipment 调查与远端 LangSmith Trace 按既定规则延期到 Phase 9。  
> 事实来源：`V2/building-plan-V2.md`。

## 1. 已完成的实现

### Task 07 — 真实发货数据流

- 新增 Warehouse 服务、发货 migration 和 Compose 服务，建立 Merchant dispatch task、Warehouse order、Warehouse shipment、Shipment delivery、Merchant receipt/task/shipment 和 Platform shipment 等独立事实。
- 正常商家订单会在同一事务安排送仓任务；已有订单可以显式 dispatch。Worker 通过真实 HTTP Contract 把商家订单送到 Warehouse。
- 仓库操作者只能对已接收订单发货；一次真实出库会保存 Warehouse shipment，再通过 HTTP 把 Shipment Event 送回 Merchant。
- Merchant 保存 receipt/task 和自己的 Shipment，再由独立 Worker 推送 Platform；Platform 使用稳定 request ID 和 payload hash 去重。
- 重复送仓不会新增 Warehouse order；重复相同发货返回原结果；同订单不同运单数据返回冲突；同步关闭时 Warehouse 和 Merchant 保留真实事实，Platform 不会被伪造为成功。
- 已接入 Lab CLI：dispatch、shipment create/show、shipment sync 开关。

### Task 08 — 发货调查基础

- Support Agent 新增 `GetShipment`、`GetShipmentRecords`、`GetPlatformShipment` 三个只读 Tool，不提供仓库出库或平台写 Tool。
- Evidence 新增对象类型、对象 ID、观察时间和来源版本；发货 Evidence 可以区分 Warehouse、Merchant 和 Platform 来源。
- 普通代码会检查 Shipment ID、carrier 和 tracking number 的矛盾；存在矛盾时不允许模型输出确定根因。
- Support Prompt 明确：有运单号不等于已实际出库，timeout 只代表结果未知，不代表平台确定拒绝。
- Dev Dataset 已加入正常等待、Merchant 未收到和 Platform 未更新三类发货调查案例。

### Task 09 — 批准补传发货结果

- 新增 `recover_shipment` Proposal，保存 Warehouse shipment snapshot、订单版本、店铺版本、Evidence、十分钟有效期和稳定 idempotency key。
- Proposal 前会读取订单、Warehouse shipment、Merchant shipment、Platform shipment 和店铺状态；Platform 已满足目标时返回 `no_action_needed`。
- 开启 shipment sync 与补传指定订单是 Proposal 中两个明确步骤，不会扫描并修复整店历史。
- 只有已批准动作才能通过 Merchant repair HTTP Contract 创建 receipt/task；执行前重新检查 company scope、源订单状态/版本、Shipment 版本、三端标识和店铺版本。
- Worker 只补传 Merchant 已持有的真实 Shipment，不会调用 Warehouse 出库接口。
- Verification 独立读取 Warehouse、Merchant、Platform 三端，要求运单一致、Warehouse shipment count 为 1、恢复任务完成；15 秒内不能证实时保持 `pending`，不假报成功。

### Task 10 — 恢复韧性基础

- Action execution 增加数据库 claim/lease、稳定 request ID、attempt 计数、`unknown` 结果和 receipt reconciliation。
- 同一 Action 的并发执行者只有一个能取得有效 lease；进程退出后必须等 lease 过期才能由新执行者继续。
- Order 和 Shipment repair 都支持“请求已被 Merchant 接收但响应丢失”后的 receipt 查询与继续验证。
- Shipment Worker 对“Platform 已提交但响应丢失”保存 `unknown`，随后先读取 Platform 事实：匹配则补齐本地 receipt/task；Platform 不存在时才用原稳定请求编号安全重试。
- Platform 新增受控 response-lost 开关：先提交真实 Shipment transaction，再延迟响应，不返回伪造 timeout 结果。
- Lab 新增 `shipment_response_lost` 场景入口；场景先关闭同步并产生真实出库，保证批准恢复前 Platform 仍无 Shipment。
- Worker 启动时会恢复处于 `processing` 的订单、dispatch、shipment、order recovery 和 shipment recovery task。

## 2. 主要文件

- Migration：`db/004_shipments.sql`
- Warehouse：`services/warehouse.py`
- Platform / Merchant / Worker：`services/platform.py`、`services/merchant.py`、`services/worker.py`、`services/common.py`
- Agent / Evidence / Action / Verification：`app/tools.py`、`app/evidence.py`、`app/support.py`、`app/actions.py`、`app/verify.py`
- CLI / 场景：`app/cli.py`、`lab/cli.py`、`lab/scenarios.py`、`compose.yaml`
- 测试：`tests/test_shipments.py`、`tests/test_shipment_actions.py`、`tests/test_actions.py`、`tests/test_handoff.py`
- 产品与评估资料：`docs/product/06-shipment-facts.md`、`prompts/support.md`、`evals/dev.jsonl`

## 3. 当前已接通的真实数据流

```text
Platform paid order
  → HTTP order event
  → Merchant receipt/task/order
  → dispatch task
  → HTTP Warehouse order
  → Warehouse operator creates one real shipment
  → Warehouse shipment + delivery record
  → HTTP Shipment Event
  → Merchant receipt/task/shipment
  → Worker HTTP push
  → Platform shipment
  → three independent readbacks
```

批准恢复路径：

```text
Support case + Shipment Evidence
  → recover_shipment Proposal
  → deterministic Policy Check
  → Human Approval
  → order/shop/shipment scope and version recheck
  → execution claim + stable request ID
  → Merchant repair receipt/task
  → Worker resends existing shipment fact only
  → receipt reconciliation
  → Warehouse/Merchant/Platform Verification
```

## 4. 已完成的验证

- `python -m pytest -q`：最新工作树 **58 项通过**。
- `tests/test_shipments.py`：5 项通过，覆盖正常三端数据流、重复保护、同步关闭、不存在 Warehouse order，以及 Warehouse/Merchant/Platform internal endpoint 跨公司隔离。
- `tests/test_shipment_actions.py`：5 项通过，覆盖批准恢复、无批准不写、Platform 提交后响应丢失、批准后源订单变化、实际子进程终止与 lease 接管。
- 新增 Order repair response-lost 定向测试通过：Merchant 已保存 receipt 但调用方丢失响应后，恢复流程先查询 receipt，再继续 Worker 与 Verification，最终只有一条商家订单。
- `python -m ruff check .`：通过。
- 测试套件从空 PostgreSQL 数据库顺序执行 migration，`db/004_shipments.sql` 已在该路径验证。
- 本地真实 HTTP/Worker 案例 `O-PH6-HTTP-0918` 成功：Platform order、Merchant order、Warehouse order、Warehouse shipment、Merchant shipment 和 Platform shipment 均来自实际服务调用；三端 tracking number 一致，Warehouse shipment count 为 1。
- 本地 Compose `shipment_response_lost` 案例 `O-SHIPMENT-RESPONSE-LOST` 成功：Platform transaction 提交后延迟 6 秒，Worker 保存未知结果并通过查询对账；recovery result 为 `reconciled: true`，最终 Action 为 `verified_resolved`。
- 对账后及重复恢复后的数据库计数均为：Warehouse shipment 1、Merchant shipment 1、Platform shipment 1、shipment repair receipt 1、action execution 1。

## 5. 模型与 LangSmith

- 本次 Phase 6 开发与已执行验收没有调用 Groq 或 Google；调查规划相关回归继续使用 deterministic mock/stub。
- 新增业务节点继续使用 LangSmith-compatible `traceable` 和本地 trace ID / action ID / request ID / receipt / Evidence metadata。
- 本次没有执行新的远端 LangSmith Trace 查询，也没有宣称远端 Trace 已验收。容器恢复验收明确使用 `LANGSMITH_TRACING=false`；三条真实 LLM Shipment 调查和远端 Trace 标记为 **External Acceptance Deferred**，统一留到 Phase 9。

## 6. External Acceptance Deferred 与已知边界

- Task 08 的三条真实 LLM Shipment 调查路径及远端 LangSmith Trace 尚未运行，统一留到 Phase 9；本地 Tool、Evidence、冲突保护、预算和 deterministic tests 已完成。
- 当前只模拟一个包裹的完整发货，不查询真实物流，不实现拆单。
- 不承诺严格 Exactly-once Execution；当前目标是 Effectively-once Business Effect。
- 没有进入或实现 Phase 7。

## 7. 当前运行状态与边界

- 本地 Compose 的 Database、Platform、Merchant、Warehouse 和 Worker 在验收结束时处于运行状态；未自动停止或删除数据。
- 不承诺严格 Exactly-once Execution；当前目标是通过稳定请求编号、唯一约束、claim/lease、receipt/reconciliation 和 read-after-write verification 达到 Effectively-once Business Effect。
- Warehouse 实际出库仍只允许测试操作者触发；Support Agent 和恢复 Worker 都没有第二次出库能力。
- V1 未修改、删除、移动，也不是 V2 的运行依赖。

## 8. 下一阶段

Phase 7 / Task 11–12 将在用户确认后处理渠道错误预算和库存只读调查。当前停止，不自动进入 Phase 7。
