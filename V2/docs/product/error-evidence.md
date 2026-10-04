---
title: 错误证据与当前事实判断
company_id: company-a
product: merchant-console
version: "2.0"
status: 当前
last_reviewed: 2026-10-04
topic: Error / Evidence / Missing Results / Scope
effective_from: 2026-01-01
effective_to: null
---

# 错误证据与当前事实判断

公司：company-a | 产品：merchant-console | 版本：2.0 | 状态：当前
最后核对日期：2026-10-04 | 主题：Error / Evidence / Missing Results / Scope

## 适用范围（Scope）

本文章说明如何解释工具结果、错误与历史记录，防止将未知事实写成确定结论。错误码逐项参考 error-codes.xlsx；这里关注证据的时间、对象、来源和可信边界。支持人员应分别记录已确认、可能、已排除和仍未知的事项。

## 业务规则（Business Rules）

当前读取包括 GetOrder、GetOrderProcessRecords、GetShopSyncStatus、GetShopConnectionStatus、GetWorkerTask、GetWarehouseShipment、GetShipmentProcessRecords、GetPlatformShipment、GetStockStatus。每条证据应携带公司、店铺和目标，保留工具、请求、响应、HTTP 状态、request_id、时间与来源服务。

success 是读取成功，仍需查看具体字段；empty 是成功但没有有效结果；not_found 是指定范围未找到；forbidden 是认证或权限拒绝；error 是其他 HTTP 读取失败；unavailable 是请求异常等导致未获得响应。HTTP 404、401/403、其他非成功及空响应分别映射到相应结果，不能统一称为业务失败。

read response 的 retryable 可来自 429、500、503 等服务错误标记，指读取请求层面的暂时性，不等于 GetWorkerTask 的订单业务重试资格。task_retryable 的规则有独立状态和错误限制。候选恢复必须引用当前案例实际证据，且工具、店铺和对象范围匹配。模型生成的错误解释或用户描述不能替代后台事实。

## 状态与判断条件（States / Conditions）

Historical Error ≠ Current Failure。渠道失败记录说明某次请求失败，当前连接与任务可能已经改变。应将最近成功读取与历史错误分别描述；旧错误不能证明当前授权仍过期或所有店铺仍中断。

Timeout ≠ Definite Failure。读超时说明当前没有取得事实；发货提交超时可能已经在平台提交，需核对平台与回执。Missing Result ≠ Object Does Not Exist。错误范围、权限、目标不明确或接口不可达都可能造成没有结果，不能当成明确不存在。

空 merchant_sku 不证明订单映射缺失；商家发货记录存在不证明平台已回传；订单 completed 不证明仓库已出库；库存量不同不证明必须刷新。完整结论应查看相关对象、版本、数量、内容和当前条件，而不是寻找单个看似有力的字段。

## 用户下一步（What the user should do next）

先确认用户要查的店铺、订单或 SKU。读取失败时说明哪条事实还未知，补成功读取或请求用户核对标识；不要扩大范围搜索另一个店铺来凑答案。访问被拒应保留权限问题，不建议绕过授权。

拿到当前事实后，与历史错误分开核对。确认存在阻断条件才能给出对应下一步；满足安全快照后才提出写动作。无法确认来源或发现多端冲突时停止恢复并转人工。工单应列明请求标识、时间、已确认事实与待验证项，不把推测列成最终根因。

## 系统不保证什么（What the system does NOT guarantee）

系统不保证单条成功读取证明整条链路完成，不保证 HTTP retryable 表示任意业务可重试，不保证历史错误持续有效。查读失败不会自动转换成对象不存在，也不会自动创建替代业务对象。数据不足时可以明确说明未知，不能为了给出确定答案编造状态。

## 业务例子（Example）

例一：读取被拒。Situation：GetOrder 返回 forbidden。Known Facts：没有成功取得来源订单。What the system can do：说明访问受限并补权限证据。What the system must not claim：不能说订单不存在或未付款。

例二：历史授权错误。Situation：工单有旧 CHANNEL_AUTH_EXPIRED。Known Facts：最新连接 authorized。What the system can do：将旧失败和当前状态分别报告，再查旧任务。What the system must not claim：不能要求无依据的再次授权。

例三：服务错误可重试。Situation：读取响应为 503 / retryable=true。Known Facts：订单任务状态尚未知。What the system can do：重新读取或人工调查。What the system must not claim：不能据此直接提出 retry_failed_task。

## 代码来源（Source）

- [backend/app/support_tools.py](../../backend/app/support_tools.py)：get_evidence_status,build_error_response,call_read_service,validate_read_tool_call
- [backend/app/support_action_plans.py](../../backend/app/support_action_plans.py)：create_action_plan
- [simulator/services/merchant.py](../../simulator/services/merchant.py)：task_retryable
- [backend/app/tickets.py](../../backend/app/tickets.py)：excluded_causes
