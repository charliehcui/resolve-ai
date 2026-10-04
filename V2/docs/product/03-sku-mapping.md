---
title: 订单 SKU 映射与库存规则的区别
company_id: company-a
product: merchant-console
version: "2.0"
status: 当前
last_reviewed: 2026-10-04
topic: SKU Mapping / Merchant SKU / Warehouse SKU
effective_from: 2026-01-01
effective_to: null
---

# 订单 SKU 映射与库存规则的区别

公司：company-a | 产品：merchant-console | 版本：2.0 | 状态：当前
最后核对日期：2026-10-04 | 主题：SKU Mapping / Merchant SKU / Warehouse SKU

## 适用范围（Scope）

订单 SKU 映射把店铺的平台 platform_sku 对应到商家 merchant_sku，供订单导入建立商家订单。本文章说明映射判断与恢复边界，并区分库存使用的 warehouse_sku 规则。两个店铺可以有不同映射，不能跨店铺复用证据。

## 业务规则（Business Rules）

订单工作进程在当前公司、店铺、platform_sku 下查找 active=true 的 merchant.sku_mappings。没有启用记录时以 SKU_MAPPING_MISSING 阻断，不会自动创建或猜测映射。普通处理只使用实际查到的 merchant_sku，来源数量和金额仍来自平台订单。

订单恢复构建器通过 GetSkuMapping 读取当前映射，要求查询成功且 active=true，将 merchant_sku 写入来源快照。这个读取用于恢复条件核对，当前不属于供 Support 任意调用的通用注册工具列表。不能因此承诺用户有一个可直接执行映射变更的动作。

恢复工作进程重新查询映射；缺失会阻断，当前 merchant_sku 与快照不同会报 SKU_MAPPING_CHANGED。审批只批准特定映射条件下的恢复，不能让它覆盖后续映射变化。库存规则位于 merchant.stock_rules，使用 warehouse_sku、safety_stock、rule_version 与 active；订单映射存在不代表库存规则存在，反向也不成立。

## 状态与判断条件（States / Conditions）

GetOrderProcessRecords 中 merchant_sku 是从既有商家订单关联取得的字段。若商家订单尚未建立，这个字段可以为空，即使真实映射存在。因此“空字段 ≠ 映射不存在”。应结合实际映射查询、active 状态或已发生 SKU_MAPPING_MISSING 的当前处理证据，不能根据一个空值要求用户建映射。

历史 SKU_MAPPING_MISSING 表示那次处理没有可用映射，不证明现在仍然缺失。当前启用映射已经恢复后，还要查询订单任务：旧 blocked 不会自动重排，不能说重新处理已经完成。

映射读失败、forbidden 或超时属于证据不足；它们不同于明确 not_found。已导入商家订单若 merchant_sku 与当前来源及映射快照冲突，需要人工核对；现有恢复不会自动修改旧订单 SKU。库存查询 STOCK_MAPPING_MISSING 说的是库存规则不足，不能直接当成订单 SKU 映射缺失。

## 用户下一步（What the user should do next）

收集店铺、平台 SKU 与订单号，确认是哪种映射问题。订单导入问题检查启用商家映射及当前付款、同步、授权；库存问题检查 warehouse_sku 规则和仓库事实。若缺少映射，需要负责映射维护的人补齐配置，当前 Support 不提供自动创建、修改或批量映射动作，也没有可确认的前端菜单路线。

映射补齐后重新取证，满足条件再提出单笔 retry_order_sync；符合 retryable 的真实订单任务才能提出 retry_failed_task。方案生成后映射变化应停止旧方案并重新生成。涉及已有订单内容冲突、映射归属不明或读取始终不足时，保留事实交人工，不以重试掩盖配置问题。

## 系统不保证什么（What the system does NOT guarantee）

系统不保证按 SKU 字符串相似度自动识别映射，不会从空 merchant_sku 字段自动判断映射缺失，不会自动建立跨店铺映射或库存规则。补齐配置不会自动补历史订单，库存刷新不会补订单映射。当前代码中的初始化映射数据也不能当成完整用户维护界面的证据。

## 业务例子（Example）

例一：空字段。Situation：商家尚无订单，merchant_sku=null。Known Facts：当前映射读取成功且 active=true。What the system can do：继续检查导入资格与任务。What the system must not claim：不能说必须创建映射。

例二：历史缺失已修复。Situation：旧任务 blocked / SKU_MAPPING_MISSING。Known Facts：当前启用映射存在，旧任务未重排。What the system can do：判断是否可提出 retry_order_sync。What the system must not claim：不能说映射修好后任务自动完成。

例三：订单映射有、库存规则无。Situation：订单可导入但库存查询信息不足。Known Facts：存在 merchant_sku 映射，没有 stock_rules。What the system can do：说明需要库存规则事实。What the system must not claim：不能以订单映射代替 warehouse_sku 规则。

## 代码来源（Source）

- [simulator/services/worker.py](../../simulator/services/worker.py)：process_next_task,process_next_recovery_task
- [simulator/services/merchant.py](../../simulator/services/merchant.py)：internal_sku_mapping,internal_process_records,internal_stock_records
- [backend/app/support_action_plans.py](../../backend/app/support_action_plans.py)：get_sku_mapping,build_order_action_plan
- [backend/app/support_tools.py](../../backend/app/support_tools.py)：READ_TOOL_FUNCTIONS
- [backend/app/stock.py](../../backend/app/stock.py)：assess_stock_facts
