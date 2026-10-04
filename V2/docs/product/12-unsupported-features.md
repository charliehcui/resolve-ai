---
title: 当前能力边界与不可承诺事项
company_id: company-a
product: merchant-console
version: "2.0"
status: 当前
last_reviewed: 2026-10-04
topic: Unsupported Features / Product Boundaries
effective_from: 2026-01-01
effective_to: null
---

# 当前能力边界与不可承诺事项

公司：company-a | 产品：merchant-console | 版本：2.0 | 状态：当前
最后核对日期：2026-10-04 | 主题：Unsupported Features / Product Boundaries

## 适用范围（Scope）

本参考列出当前代码没有支持、或不能由已有行为推导出的能力。用于回答用户功能咨询并约束恢复建议。这里的边界属于 2.0 当前实现，后续新增代码后需要重新核对。

## 业务规则（Business Rules）

订单仅处理单笔来源订单和单 SKU 数据，不支持全量历史自动导入、按日期批量补单、拆单、多商品明细、自动退款、订单内容覆盖或用户支付状态更新流程。已有恢复不会创建 SKU 映射，不支持批量映射、相似 SKU 自动匹配或跨店铺猜测配置。

发货恢复仅重发既有匹配回传，不执行仓库出库，不创建丢失商家发货回执，不支持多包裹、多运单、改运单或平台冲突覆盖。仓库派单失败没有注册的 retry_dispatch。发货登记没有自动扣库存或订单库存预留联动。

库存刷新只发布当前安全快照，不改物理数、预留数、安全库存或映射；不支持跨仓调拨、退款补库存、自动盘点或较新平台版本回滚。重新授权只有用户指引，没有真实 OAuth 执行器。当前渠道是模拟业务标识，不能声称已经联通真实平台和仓库。

## 状态与判断条件（States / Conditions）

存在工作进程循环不等于 failed 或 blocked 自动重试。启动时 processing 重排与发货 unknown 自动核对是有范围的真实行为，不能扩展到全部任务。支持工单包含工程师授权与复查，但没有固定响应 SLA、通用手动转派或无条件关闭能力。数据库状态枚举不等于相应前端操作已存在。

## 用户下一步（What the user should do next）

先说明用户目标是否可由现有动作完成。能恢复时选择注册动作并检查条件；不能恢复时描述缺少的能力和可获得的当前证据，必要时人工调查。不存在的前端菜单、工具名和写接口不能当成操作指引。GetShopConnectionStatus、GetStockStatus 是当前查询名称；旧名称不得作为可调用工具输出。

## 系统不保证什么（What the system does NOT guarantee）

系统不保证恢复百分百成功、固定完成时间、授权恢复即重试、库存差异即刷新、超时即失败或缺失读取即对象不存在。文档中的业务例子只解释条件，不构成新的批量、自动化或生产集成能力。

## 业务例子（Example）

例一：用户要求自动补去年全部订单。Known Facts：只有单笔 retry_order_sync。What the system can do：说明单笔调查范围。What the system must not claim：不能承诺全量补齐。

例二：用户要求重发物流。Known Facts：已有仓库运单，平台缺失。What the system can do：检查 resend_shipment。What the system must not claim：不能把回传重发解释为仓库再次发包裹。

## 代码来源（Source）

- [backend/app/support_action_registry.py](../../backend/app/support_action_registry.py)：ACTION_REGISTRY
- [backend/app/support_tools.py](../../backend/app/support_tools.py)：READ_TOOL_FUNCTIONS
- [simulator/services/common.py](../../simulator/services/common.py)：OrderCreate,ShipmentCreate
- [simulator/services/warehouse.py](../../simulator/services/warehouse.py)：create_shipment
- [simulator/services/worker.py](../../simulator/services/worker.py)：run_forever,recover_interrupted_tasks
- [backend/app/tickets.py](../../backend/app/tickets.py)
