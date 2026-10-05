---
title: 产品版本与知识适用性
company_id: company-a
product: merchant-console
version: "2.0"
status: 当前
last_reviewed: 2026-10-04
topic: Version / Current Scope / Historical Sources
effective_from: 2026-01-01
effective_to: null
---

# 产品版本与知识适用性

公司：company-a | 产品：merchant-console | 版本：2.0 | 状态：当前
最后核对日期：2026-10-04 | 主题：Version / Current Scope / Historical Sources

## 适用范围（Scope）

当前知识针对 company-a 的 merchant-console 2.0，核对日期为 2026-10-04。版本边界以 V2 当前业务代码为准，不将旧资料或未来规划当成现有能力。

## 业务规则（Business Rules）

当前代码实现订单、仓库、平台与工作进程的本地模拟业务，以及支持恢复与人工工单。它足以核对本文档中的具体状态与条件，但不证明已经集成真实电商渠道、仓库、授权服务或生产服务时限。

每个主题只保留一个主要来源：PDF 负责业务指南，DOCX 负责处理流程，XLSX 负责结构化参考，Markdown 负责短专题。产品目录记录主文件和被合并旧文件。各文档的来源列指向业务实现，不以 Evaluation Case 作为能力依据。

## 状态与判断条件（States / Conditions）

status=当前 的 2.0 文档适用于当前行为。14-legacy-order-sync.md 标为历史 1.0，保留旧版本背景但不用于指导 2.0 恢复和审批。最后核对日期表示最近一次代码核对，不是内容在该日期之后永远正确的承诺。

## 用户下一步（What the user should do next）

先核对版本、状态、公司、主题和来源。旧案例引用已迁移文件时，按 product-catalog.md 找到新的主来源；后续检索基础建设需要更新来源映射和重建索引。新功能上线或规则变化时应重新核对对应业务主题。

## 系统不保证什么（What the system does NOT guarantee）

本次内容整理不实现多格式解析、元数据提取或索引刷新，不代表当前 Markdown 导入器已可读取 PDF、DOCX、XLSX。没有改动检索或 Agent 流程，也没有运行 Evaluation。不能把文档创建成功当作在线检索已生效。

## 业务例子（Example）

Situation：搜索到 1.0 的历史开关规则。Known Facts：记录标为历史，当前产品为 2.0。What the system can do：转查当前同步设置与恢复流程。What the system must not claim：不能用历史管理员规则覆盖当前用户确认策略。

## 代码来源（Source）

- [backend/app/customer_document_ingestion.py](../../backend/app/customer_document_ingestion.py)
- [backend/app/support_action_registry.py](../../backend/app/support_action_registry.py)
- [simulator/services/worker.py](../../simulator/services/worker.py)
- [docs/product-catalog.md](../../docs/product-catalog.md)
