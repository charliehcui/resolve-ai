---
title: 恢复确认、管理员批准与方案有效期
company_id: company-a
product: merchant-console
version: "2.0"
status: 当前
last_reviewed: 2026-10-04
topic: Approval / User Confirmation / Admin / Expiry
effective_from: 2026-01-01
effective_to: null
---

# 恢复确认、管理员批准与方案有效期

公司：company-a | 产品：merchant-console | 版本：2.0 | 状态：当前
最后核对日期：2026-10-04 | 主题：Approval / User Confirmation / Admin / Expiry

## 适用范围（Scope）

本文章说明谁可以批准哪个方案，以及哪些行为必须重新确认。审批范围包括方案锁定的公司、店铺、对象和具体操作。用户确认、管理员批准、店铺授权人与工程师只读权限是不同概念，不能互相替代。

## 业务规则（Business Rules）

retry_order_sync、resend_shipment、refresh_inventory、retry_failed_task 默认风险 low、approval_requirement=user_confirmation。staff 可以在自己的会话范围内确认普通低风险方案；admin 可按同公司动作访问规则批准方案。若方案包含 enable_order_sync 或 enable_shipment_sync，策略改为 medium / admin，staff 普通确认不能执行。

request_reauthorization 是 user_action，向店铺授权人提供指引，不是一个后台审批后自动执行的授权操作。engineer 无权批准商家恢复，其工单读取授权也不会变成写入或审批许可。

确认必须针对实际存在且当前可用的方案。会话处理寻找最近方案，并识别明确确认或拒绝；“可以调查”“先看看”不是可推导的执行许可。涉及设置变更要在方案中明示，用户拒绝改设置不能被“同意重试”覆盖。重复确认同一动作受既有决策和幂等控制，不应产生第二次有效业务写入。

## 状态与判断条件（States / Conditions）

proposed 表示候选方案，approved 表示批准记录，rejected 表示拒绝，expired 表示有效期已过。方案从创建时起 10 分钟有效，批准不会重置截止时间。approved 也不保证条件在实际执行时仍满足，来源或店铺快照变化会阻断。

身份允许读取方案不等于允许为别人的会话创建任意方案。方案构建需要当前用户拥有对应支持案例上下文；管理员对同公司方案的访问与批准权限不能扩展成跨用户会话的无限访问。不同公司、店铺、订单或 SKU 必须保持各自范围。

审批过期、缺失批准、身份不符合或用户拒绝时，不执行原方案。已 executing 或 awaiting_verification 时先核对原执行结果，不能凭再一次“同意”生成并行提交。审批记录证明谁批准了什么，不证明商家订单、运单或库存已经修复。

## 用户下一步（What the user should do next）

向用户说明动作名称、目标对象、预期改变、是否开启同步及审批要求，再请求针对该方案的明确决定。普通单笔恢复由适格用户确认即可；含店铺设置变更的方案交管理员批准。缺少实际提案或对象时先完成调查，不能用泛化“以后都同意”替代具体方案。

执行前重新核对有效期和快照，发生变化则重新生成提案并按新范围确认。拒绝后停止该方案；过期后不能修改时间继续复用旧批准。执行已受理时查看回执和验证结果，验证失败且无法安全恢复时整理人工工单。工程师可以补证据，但商家审批仍须走原角色边界。

## 系统不保证什么（What the system does NOT guarantee）

系统不要求所有恢复都经过管理员，也不允许没有批准的设置变更。一次批准不授权全部历史订单、其他店铺或任意未来操作。不会因为用户重复确认而保证新执行，不会因方案 approved 就跳过数据版本检查或自动宣布成功。不存在通用人工“标记已修复”来替代验证。

## 业务例子（Example）

例一：普通员工恢复单笔订单。Situation：low 方案不改设置。Known Facts：属于员工自己的会话。What the system can do：接受明确确认并按条件执行。What the system must not claim：不能说必须管理员审批。

例二：普通员工同意开启同步。Situation：方案 enable_order_sync=true。Known Facts：approval_requirement=admin。What the system can do：说明需要管理员批准。What the system must not claim：不能以普通确认执行开关变更。

例三：批准时已经过期。Situation：创建 12 分钟后收到同意。Known Facts：原方案有效期已过。What the system can do：重新调查并生成新方案。What the system must not claim：不能说批准后还可再用 10 分钟。

## 代码来源（Source）

- [backend/app/support_action_registry.py](../../backend/app/support_action_registry.py)：action_policy
- [backend/app/support_action_approvals.py](../../backend/app/support_action_approvals.py)：authorize_action_decision,decide_action_plan,explicit_confirmation,respond_to_action_plan
- [backend/app/support_action_store.py](../../backend/app/support_action_store.py)：get_action_plan
- [backend/app/support_action_plans.py](../../backend/app/support_action_plans.py)：get_recovery_case_context
