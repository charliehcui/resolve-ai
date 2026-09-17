# Phase 5 / Task 06 完成报告

> 完成日期：2026-09-17  
> 状态：Complete。  
> 事实来源：`V2/building-plan-V2.md`。

## 1. 完成内容

- 新增单笔 `recover_order` Action Proposal，保存公司、店铺、订单、源事件、源版本、店铺版本、Evidence、十分钟有效期和稳定 idempotency key。
- Proposal 前由普通代码重新查询平台订单、商家处理记录、店铺同步状态、连接状态与 SKU 映射；已正确进入管理软件的订单返回 `no_action_needed`，不创建多余方案。
- 只有同公司管理员可以批准或拒绝。普通员工不能批准；拒绝和过期均不会创建修复请求。
- 批准后重新检查源订单和店铺版本。平台订单取消、支付状态或版本变化、店铺版本变化都会阻止执行。
- Merchant 新增 `POST /repairs/orders` 与回执读取 Contract。固定 action/request ID、唯一约束和 request hash 阻止重复请求产生第二次业务效果。
- Worker 领取恢复任务后再次检查批准有效期、源订单版本、店铺版本、付款状态、连接状态和 SKU 映射，再写入唯一商家订单。
- 是否开启订单同步为 Proposal 中的显式独立选项，不会隐含修改影响后续订单的店铺开关。
- 独立 Verification 从 Platform 与 Merchant 重新读取事实，核对事件、SKU、数量、金额、任务完成状态和唯一订单数。只有全部通过才写入 `verified_resolved`。
- 保存 Proposal、Policy Check、Human Approval、Scope/Version Recheck、Execution、Receipt/Reconciliation 与 Verification 的步骤、Evidence/receipt、request ID、状态、错误和可用 trace metadata。

## 2. 主要文件

- 数据库：`db/003_actions.sql`
- 业务逻辑：`app/actions.py`、`app/verify.py`
- CLI：`app/cli.py`
- 服务与 Worker：`services/common.py`、`services/platform.py`、`services/merchant.py`、`services/worker.py`
- 测试：`tests/test_actions.py`、`tests/test_verify.py`、`tests/conftest.py`
- 文档：`README.md`、`docs/product/11-approval-boundary.md`、`docs/acceptance-matrix.md`

## 3. 当前真实数据流

```text
Support case + Evidence
  → CLI action propose
  → deterministic Policy Check
  → persisted Action Proposal (10-minute expiry)
  → CLI action decide --decision approve/reject
  → admin/company/expiry check
  → source order + shop version recheck
  → stable execution claim + request ID
  → Merchant POST /repairs/orders
  → receipt + idempotent recovery task
  → Worker rechecks approval/version/business eligibility
  → unique merchant order write
  → Platform/Merchant independent readback
  → deterministic Verification
  → verified_resolved or verification_failed
```

## 4. 测试与真实运行

- `python -m pytest`：46 项通过。
- `python -m ruff check .`：通过。
- 测试数据库从空库顺序运行全部 migration。
- 自动测试覆盖：完整批准恢复、重复批准、普通员工拒绝、明确拒绝、十分钟过期、批准前源版本变化、批准状态持久化后继续执行、repair contract 去重、显式开启同步、已满足目标时不创建方案、读回失败不得标记成功。
- 本地真实纵向案例 `O-P5-REAL-1`：先停止 Merchant 产生“平台有单、商家无 receipt”的真实故障；恢复 Merchant 后创建 Action，管理员批准，HTTP repair receipt 被 Worker 处理，最终状态为 `verified_resolved`。
- 重复批准同一 Action 后，数据库仍只有 1 个 decision、1 个 repair receipt 和 1 个 merchant order。

## 5. 模型与追踪

- Phase 5 的 Proposal、Policy、Approval、Execution 和 Verification 没有使用 LLM；所有安全与成功判断均为普通代码。
- 按单独要求将 fallback 改为 `gemini-3.6-flash`，并仅运行一次最小真实 Support Tool Calling；模型正确返回一个 `GetOrder` 调用。
- 因 LangSmith 月度远端 Trace 配额 429，本阶段业务验收关闭远端上传。数据库仍保存 action ID、request ID、execution、receipt、Evidence、step、错误类型和可用 trace metadata。远端集中验收延期到 Phase 9/最终验收。

## 6. 已知边界

- 本阶段只恢复订单，不实现发货恢复。
- execution claim/lease、receipt、reconciliation 和 stable idempotency key 已建立；完整 response-lost、worker crash、并发 lease 回收统一留到 Phase 6。
- 未知 SKU 映射和失效店铺授权不会自动修复；系统会阻止 Proposal 或 Worker 写入。
- CLI 是当前入口；完整 versioned API 与 React UI 仍在 Phase 8。
- 不宣称严格 Exactly-once Execution，只保证当前实现范围内的 Effectively-once Business Effect。
- V1 未修改、删除、移动或成为 V2 运行依赖。

## 7. 下一阶段

Phase 6 / Task 07–10 将在用户确认后实现发货真实数据流、发货调查与批准恢复，并统一补齐订单/发货的 response-lost、worker crash 和并发恢复。当前没有提前实现这些能力。
