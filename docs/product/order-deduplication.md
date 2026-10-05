---
title: 订单重复投递与内容冲突
company_id: company-a
product: merchant-console
version: "2.0"
status: 当前
last_reviewed: 2026-10-04
topic: Order Deduplication / Idempotency / Content Conflict
effective_from: 2026-01-01
effective_to: null
---

# 订单重复投递与内容冲突

公司：company-a | 产品：merchant-console | 版本：2.0 | 状态：当前
最后核对日期：2026-10-04 | 主题：Order Deduplication / Idempotency / Content Conflict

## 适用范围（Scope）

本文章解释订单创建、事件接收、商家订单及恢复动作各自的幂等边界。重复请求安全性依赖业务身份和内容，不能用“系统会去重”来承诺任意重复提交都没有后果。退款、覆盖订单或批量去重不在本范围。

## 业务规则（Business Rules）

平台创建按公司、店铺、external_order_id 检查既有来源。相同内容的重复创建返回 duplicate，并再次投递同一订单事件；不同 SKU、数量、金额或付款内容会返回冲突，不覆盖既有订单。来源先保存，再发送事件，所以投递失败和来源不存在是不同状态。

商家接收按 event_id 与 payload_hash 去重。相同事件、相同内容返回 duplicate 及已有任务状态；相同事件、不同内容报 409 冲突。已有 failed 或 blocked 任务不会被重复投递自动重置。商家订单建立以 event_id 唯一约束避免同事件重复插入，仓库派单以 merchant_order_id 避免重复排队。

恢复方案还使用公司、店铺、订单、来源事件与版本、快照和设置选项生成幂等键，并复用尚有效或执行中的方案。执行使用稳定 action_id 查询回执，避免重入。请求编号不同不代表可以忽略已存在的相同业务目标；后台唯一性与独立验证仍然需要。

## 状态与判断条件（States / Conditions）

duplicate 表示相同输入已存在，不等于这个对象当前已经完成，也不意味着失败任务已重新运行。EVENT_CONTENT_CONFLICT 表示既有事件与当前拟恢复内容不一致；ORDER_UNIQUENESS_FAILED 表示恢复后唯一性检查不满足，不能按成功关闭问题。

已存在商家订单需要核对当前来源内容与完成条件。相同订单号但数量或金额不符必须人工处理；不能指望重复创建或重试自动覆盖。版本变化表示旧批准依据可能过时，应重新读取而不是制造新来源事件绕开冲突。

恢复请求超时也不能直接重复提交。先核对同 action_id 回执与实际订单是否存在；回执读失败说明仍未知。存在有效执行租约时等待，已经完成则返回既有结果，不能声称再确认就会创建第二笔订单。

## 用户下一步（What the user should do next）

提供精确公司、店铺、外部订单号和请求发生时间。先查平台来源和商家处理结果，区分相同内容重复、内容冲突、任务未完成与恢复结果未知。正常相同事件不需要另建订单；缺失且符合条件时使用正式单笔恢复方案。

发现冲突或多条商家记录时，保存事件、数量、金额、版本和回执证据，停止自动恢复并交人工。不要建议删除数据库记录、修改事件号或重复创建平台订单来绕过规则。批准一个恢复方案也不赋予覆盖已有内容的权限。

## 系统不保证什么（What the system does NOT guarantee）

系统不保证重复创建会恢复失败任务，不保证换 request_id 能安全重复同一动作，也没有自动合并冲突订单、覆盖来源内容或清理重复订单能力。幂等防护不等于业务完成证据，仍应核对商家唯一性及任务状态。

## 业务例子（Example）

例一：重复平台创建。Situation：相同订单重复提交。Known Facts：来源内容完全一致，商家旧任务 failed。What the system can do：返回重复来源并重新投递同一事件。What the system must not claim：不能说旧失败任务因此已重新执行。

例二：相同订单号、不同数量。Situation：第二次提交数量变化。Known Facts：平台已有原始来源。What the system can do：返回冲突并要求人工核对。What the system must not claim：不能承诺自动覆盖。

例三：恢复结果未知。Situation：执行请求超时。Known Facts：action_id 已固定。What the system can do：读取既有回执及订单事实。What the system must not claim：不能立即生成第二笔恢复来“确保成功”。

## 代码来源（Source）

- [simulator/services/platform.py](../../simulator/services/platform.py)：create_order
- [simulator/services/merchant.py](../../simulator/services/merchant.py)：store_order_event,receive_order_repair
- [simulator/services/worker.py](../../simulator/services/worker.py)：queue_dispatch,process_next_recovery_task
- [backend/app/support_action_plans.py](../../backend/app/support_action_plans.py)：reusable_plan,build_order_recovery_idempotency_key
- [backend/app/support_action_execution.py](../../backend/app/support_action_execution.py)
