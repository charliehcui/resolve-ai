---
title: 发货请求结果未知与安全核对
company_id: company-a
product: merchant-console
version: "2.0"
status: 当前
last_reviewed: 2026-10-04
topic: Shipment Unknown / Timeout / Reconciliation
effective_from: 2026-01-01
effective_to: null
---

# 发货请求结果未知与安全核对

公司：company-a | 产品：merchant-console | 版本：2.0 | 状态：当前
最后核对日期：2026-10-04 | 主题：Shipment Unknown / Timeout / Reconciliation

## 适用范围（Scope）

本文章专门解释发货回传提交后没有收到可靠响应的情况。unknown 表示本次写请求结果未知，与明确失败、平台无发货、仓库未出库不同。这里描述代码真实存在的发货自动核对，不把它扩展成任意失败任务自动重试。

## 业务规则（Business Rules）

商家回传使用基于公司和 shipment_id 的稳定 platform_request_id；恢复使用已有匹配发货快照。实际向平台提交时发生超时，普通或恢复发货任务可记为 unknown / PLATFORM_RESPONSE_LOST。因为平台可能已经提交，不能直接向用户报告“发货一定失败”。

工作进程循环中的 reconcile_next_unknown_shipment 选择未知发货任务并查询指定店铺和订单的平台发货事实。平台记录的 shipment_id、carrier、tracking_number 与原快照一致时，将相关任务 completed，保存核对回执；明确 404 时将原任务重排为 pending，再按原业务标识发送。

查询异常、其他 HTTP 状态或平台记录不匹配时，任务保持 unknown。核对不是覆盖平台事实，也不会重新指挥仓库出库。后台运行依赖工作进程持续运行和服务可访问，没有固定核对完成时间。自动核对比完整恢复验证检查少，核对匹配不替代三端版本、唯一性等最终条件。

## 状态与判断条件（States / Conditions）

CHANNEL_TIMEOUT 可能是工作进程根据当前连接 timeout 在提交前拒绝，也可能是库存请求超时错误；它不等价于发货写入后的 PLATFORM_RESPONSE_LOST。必须看操作、请求阶段和任务状态，不能把所有“timeout”按同一恢复办法处理。

GetPlatformShipment 的读取超时只说明平台事实当前未知，不足以判定平台无记录。只有明确 not_found/404 才支持“指定范围平台没有发货记录”的结论。商家回执存在不证明平台成功，平台记录匹配也不能证明所有仓库版本一致。

当平台运单与快照冲突，不能自动重发覆盖。仓库、商家标识或版本不一致也不能安全恢复。若仓库实际已发货但商家缺失匹配记录，当前 resend_shipment 构建条件不满足，需人工核对事件链路。

## 用户下一步（What the user should do next）

先确认公司、店铺、订单号、shipment_id、运单及原请求标识。查看仓库、商家和平台三端事实，说明目前是提交结果未知还是确有缺失。unknown 且核对正在进行时先等待并复查，不马上建第二次出库或并行写请求。

平台已匹配时检查完整目标状态，符合则无需重发；明确缺失且三端来源可用时，可以判断现有任务重排或正式 resend_shipment 的条件。读失败、内容冲突、核对长期无法获得明确事实或缺少商家记录时，整理请求与证据交人工。执行请求层面未知还应先读取原 action_id 回执，不能换编号强制提交。

## 系统不保证什么（What the system does NOT guarantee）

系统不保证超时等于失败或成功，不保证所有 unknown 自动变 completed，不保证读失败时立即安全重发，也不会自动改运单、生成新包裹或覆盖冲突平台记录。自动核对针对发货未知任务，不能当成 failed 订单、库存或派单的自动重试机制。

## 业务例子（Example）

例一：平台已写入但响应丢失。Situation：任务 unknown。Known Facts：平台 shipment_id、承运商、运单与快照一致。What the system can do：自动核对并完成相关任务，再检查完整验证。What the system must not claim：不能再次出库。

例二：核对明确 404。Situation：平台没有该订单发货记录。Known Facts：未知任务的原快照仍在。What the system can do：重排原任务并使用稳定标识。What the system must not claim：不能说新建了另一份发货事实。

例三：平台查询仍超时。Situation：尚未读到结果。Known Facts：平台是否提交未知。What the system can do：保留 unknown，继续补证据或人工处理。What the system must not claim：不能把查读超时当成 404。

## 代码来源（Source）

- [simulator/services/merchant.py](../../simulator/services/merchant.py)：receive_shipment_event
- [simulator/services/worker.py](../../simulator/services/worker.py)：channel_failure,process_next_shipment_task,process_next_shipment_recovery_task,reconcile_next_unknown_shipment
- [backend/app/support_action_execution.py](../../backend/app/support_action_execution.py)
- [backend/app/support_action_verification.py](../../backend/app/support_action_verification.py)：check_shipment_recovery_facts
