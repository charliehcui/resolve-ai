---
title: 库存版本、数量差异与信息不足
company_id: company-a
product: merchant-console
version: "2.0"
status: 当前
last_reviewed: 2026-10-04
topic: Inventory Versions / Quantity Mismatch / Source Evidence
effective_from: 2026-01-01
effective_to: null
---

# 库存版本、数量差异与信息不足

公司：company-a | 产品：merchant-console | 版本：2.0 | 状态：当前
最后核对日期：2026-10-04 | 主题：Inventory Versions / Quantity Mismatch / Source Evidence

## 适用范围（Scope）

本文章专门讨论 GetStockStatus 的版本和证据边界，解释为什么“库存不同”并不总适合刷新。数量公式、正常发布和通用刷新步骤以 inventory-guide.pdf 为主要来源，本篇不另建一套规则。目标仍是同公司、店铺和平台 SKU。

## 业务规则（Business Rules）

仓库 version 表示来源快照，平台 source_version 表示发布所依据的仓库版本；只有同一库存对象的这两个字段可以做来源比较。rule_version 是商家库存规则版本，店铺 version 是配置版本，都不能与仓库版本混为同一个计数器。

当前评估先检查库存规则、仓库事实、数量、版本与 updated_at。缺少规则得到 STOCK_MAPPING_MISSING；缺少仓库或规则对象得到 SOURCE_FACTS_MISSING；缺少数量、版本、时间或安全库存得到 SOURCE_VERSION_OR_TIME_MISSING；时间不能解析得到 SOURCE_TIME_INVALID。这些是证据不足，不能靠重发库存补齐。

仓库最近更新不超过 30 秒且平台来源版本缺失或不同，判为 waiting；超过窗口判为 difference。若同版本数量错误，则直接 QUANTITY_MISMATCH。平台来源版本不等的标签 VERSION_NOT_PUBLISHED 不表达方向，可能旧，也可能领先；刷新方案另检查平台版本高于仓库时拒绝。

## 状态与判断条件（States / Conditions）

平台明确 not_found 可以作为缺少已发布库存参与判断，仓库 not_found 则表示来源事实不足，两者角色不同。平台或仓库返回权限错误、超时或服务错误时，应保留读取失败；不能将它们转换成空数据再执行差异判断。

平台版本领先仓库说明当前来源可能落后或数据归属需要核查。系统禁止用 refresh_inventory 覆盖较新平台版本，应交人工调查。版本相等但数量错误可以是刷新候选，也必须满足规则完整、授权正常、无活动发布任务和快照未变化。

assessment=consistent 已满足当前计算目标，无需动作。assessment=waiting 应先复查，insufficient_information 应补数据，difference 才进入安全恢复检查。Inventory Difference ≠ Refresh Required：即使是 difference，平台领先、任务在运行或证据有冲突时也不能刷新。

## 用户下一步（What the user should do next）

向用户展示是哪两个版本和哪类数量在比较，避免只说“库存有差异”。记录仓库时间、平台来源版本、规则版本和最新任务，再选等待、补证据、刷新候选或人工。更新时间属于仓库观察依据，不是页面刷新时间；30 秒是评估窗口，不是定时自动修复承诺。

需要人工时给出完整事实，包括领先方向、对象归属与读取范围。安全恢复期间来源或规则变化要重新生成方案，不能继续按旧批准发布。若 latest task pending 或 processing，应先等待任务，不能以“平台还旧”创建第二个刷新。

## 系统不保证什么（What the system does NOT guarantee）

系统不保证 VERSION_NOT_PUBLISHED 一定意味着平台较旧，不会回滚较新版本，不会从一个数量差推导库存根因。缺少来源、时间或规则时没有安全刷新保证；当前不存在自动补仓库事实、修改安全库存或自动建立规则的 Support 动作。

## 业务例子（Example）

例一：平台领先。Situation：仓库版本 5、平台来源版本 6，已超过窗口。Known Facts：assessment=difference。What the system can do：拒绝刷新并要求人工核查。What the system must not claim：不能说发布版本 5 会修好。

例二：同版本数量错误。Situation：两端版本 8，平台量不等于当前计算量。Known Facts：QUANTITY_MISMATCH，刚更新也不豁免。What the system can do：检查是否满足 refresh_inventory。What the system must not claim：不能机械要求先等满 30 秒。

例三：仓库查读失败。Situation：来源服务超时。Known Facts：仓库数量与版本未知。What the system can do：补成功证据。What the system must not claim：不能把来源当成零库存并发布。

## 代码来源（Source）

- [backend/app/stock.py](../../backend/app/stock.py)：assess_stock_facts
- [backend/app/support_tools.py](../../backend/app/support_tools.py)：get_stock_status
- [backend/app/support_action_plans.py](../../backend/app/support_action_plans.py)：build_inventory_action_plan
- [simulator/services/platform.py](../../simulator/services/platform.py)：publish_stock
