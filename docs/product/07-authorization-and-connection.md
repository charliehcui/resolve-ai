---
title: 店铺授权与连接状态
company_id: company-a
product: merchant-console
version: "2.0"
status: 当前
last_reviewed: 2026-10-04
topic: Merchant Authorization / Connection / Channels
effective_from: 2026-01-01
effective_to: null
---

# 店铺授权与连接状态

公司：company-a | 产品：merchant-console | 版本：2.0 | 状态：当前
最后核对日期：2026-10-04 | 主题：Merchant Authorization / Connection / Channels

## 适用范围（Scope）

店铺连接状态描述当前店铺访问渠道的授权或服务条件，GetShopConnectionStatus 是当前查询工具。它不同于用户登录权限、同步开关和历史渠道失败记录。渠道 A、B 都经过同一授权检查；代码没有渠道 A 永久免授权的分支。

## 业务规则（Business Rules）

authorized 表示当前连接可通过授权检查，但仍需订单付款、映射、同步或库存事实满足相应操作条件。auth_expired 表示授权过期，forbidden 表示渠道拒绝权限；工作进程将相关任务 blocked。request_reauthorization 只针对这两种当前状态生成授权人到平台重新授权的指引。

rate_limited、internal_error、unavailable、timeout 分别表示限流、渠道内部错误、服务不可用和超时。工作进程将相应操作记为 failed，并保存渠道、店铺、操作类型、HTTP 状态、request_id 等失败证据。当前状态属于指定店铺；某次失败记录不能代表其他店铺、其他操作或渠道全局故障。

重新授权指引没有后台执行器，不自动完成权限授予、采集真实平台令牌或验证 OAuth。当前实现通过本地模拟服务记录连接状态，实验控制可设置或恢复它；这不是普通商家前端授权入口，也不是已接通真实商业平台的证据。文档只能说明授权人要处理授权，然后重新查询状态。

## 状态与判断条件（States / Conditions）

auth_expired 通常对应 401 / CHANNEL_AUTH_EXPIRED，forbidden 对应 403 / CHANNEL_FORBIDDEN；rate_limited 对应 429，internal_error 对应 500，unavailable 对应 503。连接 timeout 的工作进程错误为 CHANNEL_TIMEOUT，没有固定 HTTP 状态。这与实际发货提交超时后的 unknown / PLATFORM_RESPONSE_LOST 不同：后者可能平台已经写入。

授权查询本身返回 forbidden 可能是读取访问被拒，不一定成功读到了 connection_status=forbidden。查询超时则无法得知当前状态，不能把读取失败直接转换成授权失效。旧 CHANNEL_AUTH_EXPIRED 与当前 authorized 可以同时存在，前者是历史失败，后者是当前事实。

Authorization Restored ≠ Old Tasks Retried。授权恢复不自动重排 failed 或 blocked 任务。待处理新任务会在执行时使用当前状态；旧任务是否能恢复仍须按订单、发货或库存各自条件判断。订单任务 retryable 也不是对所有渠道错误都为真。

## 用户下一步（What the user should do next）

先确认公司和店铺，用 GetShopConnectionStatus 获取当前状态，再查看 GetShopSyncStatus 和相关业务任务。auth_expired 或 forbidden 时让店铺授权人处理平台授权，随后返回重新检查；不能由 Support 或工程师代替完成授权。

限流或服务错误时保留具体请求与操作范围，待连接事实恢复后重新判断原任务，不能承诺自动重试时间。无法读取授权事实时先查权限和服务，不提出依赖 authorized 的写动作。当前授权正常但订单或发货仍失败时，应转查付款、映射、开关、数据冲突和任务，不反复要求重新授权。

## 系统不保证什么（What the system does NOT guarantee）

系统不保证重新授权会自动补历史订单、重发所有旧运单或刷新所有库存，不支持把渠道 A 判为无需授权，也没有真实 OAuth 自动修复能力。一个店铺的失败不能扩大为全渠道中断；当前 authorized 也不保证后续外部请求一定成功。没有实际入口证据时不编造前端授权菜单。

## 业务例子（Example）

例一：渠道 A 过期。Situation：订单 blocked。Known Facts：渠道 A 当前 auth_expired。What the system can do：提出 request_reauthorization 指引。What the system must not claim：不能说渠道 A 不检查授权。

例二：历史失败已恢复。Situation：曾有 401，但当前 authorized。Known Facts：旧任务仍 blocked。What the system can do：检查具体恢复条件。What the system must not claim：不能把旧错误当成当前过期，也不能说旧任务已自动重跑。

例三：连接查询超时。Situation：未取得成功状态。Known Facts：当前授权未知。What the system can do：补读取证据或升级人工。What the system must not claim：不能说已确认授权过期。

## 代码来源（Source）

- [backend/app/support_tools.py](../../backend/app/support_tools.py)：get_shop_connection_status,call_read_service
- [backend/app/support_action_plans.py](../../backend/app/support_action_plans.py)：build_reauthorization_action
- [backend/app/support_action_registry.py](../../backend/app/support_action_registry.py)：ACTION_REGISTRY
- [simulator/services/merchant.py](../../simulator/services/merchant.py)：set_connection,restore_connection
- [simulator/services/worker.py](../../simulator/services/worker.py)：channel_failure,record_channel_failure
