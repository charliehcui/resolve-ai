---
title: 店铺订单与发货同步设置
company_id: company-a
product: merchant-console
version: "2.0"
status: 当前
last_reviewed: 2026-10-04
topic: Sync / Shop Settings / Scope
effective_from: 2026-01-01
effective_to: null
---

# 店铺订单与发货同步设置

公司：company-a | 产品：merchant-console | 版本：2.0 | 状态：当前
最后核对日期：2026-10-04 | 主题：Sync / Shop Settings / Scope

## 适用范围（Scope）

本文章说明店铺同步开关的实际作用，分别讨论订单导入与发货回传。开关只属于指定公司和店铺；一个店铺关闭或异常不能用来判断其他店铺。同步开关、连接授权与任务状态是三类独立事实，排查时应分别查询。

## 业务规则（Business Rules）

GetShopSyncStatus 返回 sync_enabled、shipment_sync_enabled 和店铺版本。订单工作进程要求 sync_enabled=true；发货回传要求 shipment_sync_enabled=true。订单处理完成会排入仓库派单队列，但开关开启不代表仓库已经出库。库存发布检查授权和库存事实，不以订单同步开关为开关。

关闭订单同步会阻断待处理订单并记录 ORDER_SYNC_DISABLED；关闭发货同步会阻断回传并记录 SHIPMENT_SYNC_DISABLED。开启开关只是恢复前提，不会扫描历史订单，也不会自动将既有 blocked 或 failed 改回 pending。

当前项目的独立开关设置接口属于本地实验控制，Support 恢复方案仅能在明确 enable_order_sync 或 enable_shipment_sync 选项下处理相应设置。这样的方案改变店铺范围设置，必须管理员批准。普通员工确认单笔恢复不能隐含授权开关变更。当前前端没有可据代码确认的“店铺设置 → 订单同步”菜单，不应提供虚构的点击路线。

## 状态与判断条件（States / Conditions）

sync_enabled=false 与 authorized 可以同时成立，说明授权正常但订单同步被关闭；sync_enabled=true 与 auth_expired 也可以同时成立，说明开关开启但连接失效。只有两个前提都满足，再结合付款、映射和任务事实，才能讨论订单处理。

任务已经 blocked 后，当前开关为 true 只证明配置现在满足，不证明旧任务已经恢复。历史错误应保留为该次尝试的信息。店铺 version 在方案期间变化时，旧提案不能继续按旧事实执行；恢复必须重新读取并生成新方案。读服务超时或 forbidden 时，不能默认开关关闭。

平台和仓库数据有各自版本，店铺 version 不是订单版本，也不是库存来源版本。不能只比较两个无关数字就宣称同步落后。需要明确哪个对象、哪个阶段、哪个字段正在比较。

## 用户下一步（What the user should do next）

先说明需要店铺标识，查询两个同步开关与 GetShopConnectionStatus。若用户明确禁止改设置，保持该限制并解释当前阻断原因。若用户希望恢复且确需开启对应开关，展示包含该选项的完整单笔方案并要求管理员批准；不要用普通用户的“重试”同意推导店铺设置许可。

配置已恢复后重新检查订单、发货或库存事实。活动任务先观察，符合当前安全条件的缺失订单或回传再提出具体动作。不具备管理员批准、目标对象不明确、配置读取失败或方案过期时，不执行开关变更，应补信息或交人工。

## 系统不保证什么（What the system does NOT guarantee）

系统不保证开启同步会补全部历史订单、重新投递丢失发货事件、创建映射或自动重试旧任务。订单开关不控制库存刷新。没有可验证的前端设置入口时，应说明当前能提供的是经过批准的恢复方案，不承诺用户可以在某个菜单自行完成所有设置。

## 业务例子（Example）

例一：订单关闭但授权正常。Situation：订单 blocked / ORDER_SYNC_DISABLED。Known Facts：authorized，sync_enabled=false。What the system can do：明确阻断条件，必要时提出含开启选项的管理员方案。What the system must not claim：不能要求用户重新授权来解决开关问题。

例二：库存差异且订单关闭。Situation：平台库存数量不符。Known Facts：库存事实完整、连接 authorized，无活动发布任务。What the system can do：按库存条件判断 refresh_inventory。What the system must not claim：不能说必须先开启订单同步。

例三：开关刚恢复。Situation：旧订单仍 blocked。Known Facts：当前 sync_enabled=true。What the system can do：检查单笔 retry_order_sync 条件。What the system must not claim：不能承诺旧订单已经自动补回。

## 代码来源（Source）

- [backend/app/support_tools.py](../../backend/app/support_tools.py)：get_shop_sync_status
- [simulator/services/merchant.py](../../simulator/services/merchant.py)：set_shop_sync,set_shipment_sync
- [simulator/services/worker.py](../../simulator/services/worker.py)：process_next_task,process_next_shipment_task,process_next_stock_task
- [backend/app/support_action_registry.py](../../backend/app/support_action_registry.py)：action_policy
- [backend/app/support_action_plans.py](../../backend/app/support_action_plans.py)：build_order_action_plan,build_shipment_action_plan
