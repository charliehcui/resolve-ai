---
title: 指定缺失与历史订单恢复
company_id: company-a
product: merchant-console
version: "2.0"
status: 当前
last_reviewed: 2026-10-04
topic: Historical Orders / Missing Orders / Single Order Recovery
effective_from: 2026-01-01
effective_to: null
---

# 指定缺失与历史订单恢复

公司：company-a | 产品：merchant-console | 版本：2.0 | 状态：当前
最后核对日期：2026-10-04 | 主题：Historical Orders / Missing Orders / Single Order Recovery

## 适用范围（Scope）

这里的“历史订单”只表示此前已经创建的来源订单，不表示产品支持全量历史导入。本文章解释针对一笔符合当前条件但缺失的订单怎样调查和恢复。没有代码支持的时间范围筛选、批量扫描、自动补历史或固定回溯天数，都不能写成产品规则。

## 业务规则（Business Rules）

指定恢复必须先有公司、店铺和订单号，再取得当前 GetOrder、GetOrderProcessRecords、GetShopSyncStatus、GetShopConnectionStatus 与启用 SKU 映射。当前平台来源必须存在且 payment_status=paid；订单曾经付过款的描述不能替代当前来源事实。

retry_order_sync 可以处理缺失的商家订单，不要求一定已有事件回执或订单工作任务；但来源、映射、配置和授权必须完整，普通 pending 或 processing 订单任务应先等待。若已有商家订单满足完整目标状态则返回 no_action_needed；现有订单与来源内容冲突不能通过恢复覆盖。

方案锁定来源 event_id、version、SKU、数量、金额、付款状态、映射 merchant_sku 及店铺版本。普通单笔恢复只需要用户确认，旧文档“任何状态修改都必须管理员批准”已不适用。仅当方案明确 enable_order_sync=true，才要求管理员审批；用户禁止改设置时不能加入这个选项。

## 状态与判断条件（States / Conditions）

平台源订单 not_found 表示在当前范围没有可恢复来源，需要确认标识或人工查证；读取错误表示来源未知。商家 empty 或 not_found 与平台来源缺失不同，不能合并成“订单不存在”。来源 unpaid 或 cancelled 不满足恢复条件，也没有把它自动改成 paid 的动作。

旧任务 blocked、failed 不会因开关、授权或映射恢复而自动重试。retry_failed_task 另有真实订单任务与 retryable 条件，不能因为订单缺失就选择这个动作。当前配置满足后，可重新判断 retry_order_sync 是否安全。

恢复方案从创建起 10 分钟内有效，执行时仍检查当前来源与配置。APPROVAL_EXPIRED、SOURCE_VERSION_CHANGED、SHOP_VERSION_CHANGED、SKU_MAPPING_CHANGED、EVENT_CONTENT_CONFLICT 等阻断要求重新取证或人工处理。既有错误只是那次尝试的记录，不能认为再次恢复一定遇到同一错误。

## 用户下一步（What the user should do next）

先区分平台来源缺失、事件投递缺失、工作任务失败和商家内容冲突。获取当前事实后，向用户说明一笔订单恢复的具体范围及是否涉及设置变更。只有明确确认当前有效方案才执行；未获批准、数据已变化或来源不满足资格时停止旧方案。

执行回执表示请求已受理，后续要验证来源未变、商家订单恰好一份、内容一致及任务 completed。短轮询未通过不能保证操作完全没有效果，应先复查同一对象和 action_id。重复失败、内容冲突、无法获得可靠来源、派单阶段异常或不存在安全动作时整理人工工单，不建议用户重复创建平台订单来绕过恢复。

## 系统不保证什么（What the system does NOT guarantee）

系统不会一键补所有历史订单，不保证开启同步立即补回旧订单，不提供按日期全量重放工具，也不会自动批准恢复、建立映射或修改付款状态。单笔恢复不承诺仓库出库和发货完成。批准不会延长原方案有效期，旧方案不能复用于另一笔订单。

## 业务例子（Example）

例一：来源存在、商家未接收。Situation：平台投递失败。Known Facts：当前 paid、启用映射、同步和授权正常，商家没有活动任务。What the system can do：提出 retry_order_sync。What the system must not claim：不能说缺少工作任务就一定不能恢复。

例二：同步关闭。Situation：合格历史订单缺失。Known Facts：sync_enabled=false。What the system can do：若允许改设置，提出明确开启选项并要求管理员批准。What the system must not claim：不能把用户同意恢复当成开关批准。

例三：来源内容变化。Situation：确认前后版本不同。Known Facts：快照不再匹配。What the system can do：停止旧方案，重新调查。What the system must not claim：不能承诺按旧金额或数量强制导入。

## 代码来源（Source）

- [backend/app/support_action_plans.py](../../backend/app/support_action_plans.py)：build_order_action_plan
- [backend/app/support_action_registry.py](../../backend/app/support_action_registry.py)：action_policy
- [backend/app/support_action_approvals.py](../../backend/app/support_action_approvals.py)：decide_action_plan
- [backend/app/support_action_verification.py](../../backend/app/support_action_verification.py)：check_order_recovery_facts
- [simulator/services/worker.py](../../simulator/services/worker.py)：process_next_recovery_task
