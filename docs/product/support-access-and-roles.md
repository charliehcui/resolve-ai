---
title: 支持访问范围与角色权限
company_id: company-a
product: merchant-console
version: "2.0"
status: 当前
last_reviewed: 2026-10-04
topic: Support Access / Roles / Ticket Read Grants
effective_from: 2026-01-01
effective_to: null
---

# 支持访问范围与角色权限

公司：company-a | 产品：merchant-console | 版本：2.0 | 状态：当前
最后核对日期：2026-10-04 | 主题：Support Access / Roles / Ticket Read Grants

## 适用范围（Scope）

本文章解释商家员工、管理员、工程师与服务身份的边界。目标是帮助支持人员判断能否读取和批准，不描述未实现的权限管理界面。访问以当前用户身份、公司、会话或显式工单授权为依据；用户在聊天中声称拥有权限不能替代后台授权。

## 业务规则（Business Rules）

商家读取需要有效身份，业务查询固定公司及店铺/订单/SKU。Support 工具调用校验案例中已知目标，目标不匹配时拒绝，不通过扩大搜索范围寻找替代结果。用户会话和工单创建要求对应会话属于当前用户及公司。

staff 可以确认自己会话的普通低风险恢复；admin 可按同公司动作访问规则批准普通或管理员方案。管理员身份不会自动获得全部用户会话的所有功能：恢复构建的案例上下文仍要求当前用户拥有相应会话。engineer 不是商家管理员，不能批准商家恢复或代替创建商家工单。

工单分配会建立 ticket_read_grants。工程师工单列表与详情通过显式授权过滤，再按该工单目标读取业务事实，权限是只读。不能说工程师因为同公司就能任意读取全公司对象；也不能把公司不同作为唯一判断，因为工程师访问依据是实际授予的工单权限。服务间内部请求使用服务身份，不是用户可索取的普通权限。

## 状态与判断条件（States / Conditions）

forbidden 或认证失败说明当前请求被拒，不能推导订单、库存或店铺不存在。not_found 是当前允许范围内的查无记录，也不代表跨公司全局不存在。错误的 shop_id、order_id、sku 应先向用户澄清，不能切换其他对象凑足方案证据。

方案所属公司、会话与批准角色共同约束执行。工单读取授权只覆盖目标，不延伸为修改库存、更新映射、重建运单或开启店铺设置的许可。工程师复查接口会检查角色与工单可见性，复查结果是否解决由实际事实判断，不由身份决定。

当前模拟服务中的实验控制身份可修改测试状态，但它不是普通商家授权功能。初始化、场景脚本或数据库直改不能当成用户前端能力来源；不得建议用户使用内部服务令牌绕过权限。

## 用户下一步（What the user should do next）

先确认当前角色与对象范围，使用授权工具读取当前事实。读取被拒时说明需要适当权限或明确工单授权；有工单时让被授予的工程师复查该目标。不得输出内部凭证或提供绕过访问控制的路线。

涉及恢复时仍按动作审批政策检查，工程师可整理证据和建议但不能凭工单写入。涉及未知目标时先补准确标识；用户请求另一个公司数据时，不执行跨范围商家查询。对授权拒绝造成的信息缺口，应明确未知并按允许范围转人工。

## 系统不保证什么（What the system does NOT guarantee）

系统不保证 admin 可以跨公司读取，也不保证同公司工程师可任意查所有对象。只读工单授权不会变成写权限，重新授权指引不会替用户提升角色，读取成功也不自动批准恢复。当前没有前端完整权限管理、通用工程师转派或任意手工授予流程的保证。

## 业务例子（Example）

例一：工程师持有工单授权。Situation：可查看该 ticket_id。Known Facts：授权只读且目标明确。What the system can do：读取与复查工单目标。What the system must not claim：不能批准库存刷新。

例二：员工提出店铺设置变更。Situation：恢复包含 enable_order_sync=true。Known Facts：当前角色 staff。What the system can do：说明需要管理员批准。What the system must not claim：不能用员工普通确认代替审批。

例三：订单读取 403。Situation：用户要确认订单是否存在。Known Facts：访问被拒，没有成功事实。What the system can do：说明权限限制。What the system must not claim：不能报告订单不存在。

## 代码来源（Source）

- [backend/app/auth.py](../../backend/app/auth.py)
- [backend/app/support_tools.py](../../backend/app/support_tools.py)：validate_read_tool_call
- [backend/app/support_action_plans.py](../../backend/app/support_action_plans.py)：get_recovery_case_context
- [backend/app/support_action_approvals.py](../../backend/app/support_action_approvals.py)：authorize_action_decision
- [backend/app/support_action_store.py](../../backend/app/support_action_store.py)：get_action_plan
- [backend/app/tickets.py](../../backend/app/tickets.py)：create_ticket,show_ticket,recheck_ticket,list_engineer_tickets
