---
title: Legacy 1.0 订单同步历史资料
company_id: company-a
product: merchant-console
version: "1.0"
status: 历史资料
last_reviewed: 2026-10-04
topic: Legacy / Historical Order Sync
effective_from: 2024-01-01
effective_to: 2025-12-31
---

# Legacy 1.0 订单同步历史资料

公司：company-a | 产品：merchant-console | 版本：1.0 | 状态：历史资料
最后核对日期：2026-10-04 | 主题：Legacy / Historical Order Sync

## 适用范围（Scope）

本文件仅保留历史 1.0 文档身份，公司 company-a，产品 merchant-console。历史有效范围为 2024-01-01 至 2025-12-31；2026-10-04 核对时状态为历史。它不提供当前 2.0 的业务操作规则。

## 业务规则（Business Rules）

旧知识曾以简短文字概括订单同步和审批。由于当前代码采用具体动作策略、证据快照、用户确认与独立验证，本次不继续保留可能误导的旧恢复指令。历史存在某段文案不证明旧前端菜单或集成能力实际被实现。

## 状态与判断条件（States / Conditions）

历史文档不应用于当前授权、等待、恢复或管理员权限判断。当前规则应读取 01-order-sync-switch.md、05-history-recovery.md、11-approval-boundary.md 和 recovery-actions.docx。

## 用户下一步（What the user should do next）

搜索命中本文件时，先告知它是历史资料，再转向当前主题来源。需要真实 1.0 行为时应核对对应旧版本代码；本轮没有审查旧版本实现，不能把旧文案恢复成确定事实。

## 系统不保证什么（What the system does NOT guarantee）

不保证 1.0 的文案适用于 2.0，不提供当前前端菜单、自动补历史、通用管理员审批或授权豁免的依据。不会因保留该文件而继续支持旧工具名。

## 业务例子（Example）

Situation：用户引用旧文档说所有恢复都需管理员。Known Facts：来源为历史 1.0，当前动作是普通低风险单笔恢复。What the system can do：以当前审批文档解释用户确认规则。What the system must not claim：不能把历史文案当成当前权限策略。

## 代码来源（Source）

- [backend/app/support_action_registry.py](../../backend/app/support_action_registry.py)：ACTION_REGISTRY,action_policy
- [backend/app/support_action_approvals.py](../../backend/app/support_action_approvals.py)：authorize_action_decision
