# company-a 产品知识来源目录

公司：company-a | 产品：merchant-console | 版本：2.0 | 状态：当前
最后核对日期：2026-10-04 | 主题：原始产品知识来源与迁移映射

目录用于导航，不计作独立业务知识来源。`docs/product/` 共 23 份主来源：22 份当前知识、1 份历史资料；PDF 3、DOCX 4、XLSX 3、Markdown 13。每个主题只有一份主要文件，不保留内容相同的多格式副本。

PDF 是业务指南；DOCX 是处理流程；XLSX 是可筛选状态、错误和渠道参考；Markdown 是专门问题与版本文章。核心来源统一包含适用范围、业务规则、状态条件、用户下一步、不保证事项、业务例子及代码来源。核心篇幅按正文汉字计数，排除标题、元数据、代码来源和英文标识；表格以有效参考条目计数。

| 主来源 | 内容职责 | 状态 | 有效内容 |
| --- | --- | --- | --- |
| [guides/order-guide.pdf](product/guides/order-guide.pdf) | 订单导入与完成判断 | 当前 / 2.0 | 1121 个中文字（核心） |
| [guides/shipment-guide.pdf](product/guides/shipment-guide.pdf) | 仓库发货与平台回传指南 | 当前 / 2.0 | 1041 个中文字（核心） |
| [guides/inventory-guide.pdf](product/guides/inventory-guide.pdf) | 库存计算、传播与安全刷新 | 当前 / 2.0 | 1000 个中文字（核心） |
| [procedures/order-worker.docx](product/procedures/order-worker.docx) | 订单工作进程与任务处理 SOP | 当前 / 2.0 | 945 个中文字（核心） |
| [procedures/recovery-actions.docx](product/procedures/recovery-actions.docx) | 安全恢复动作与结果验证 SOP | 当前 / 2.0 | 1160 个中文字（核心） |
| [procedures/waiting-and-escalation.docx](product/procedures/waiting-and-escalation.docx) | 等待、复查与人工升级 SOP | 当前 / 2.0 | 1090 个中文字（核心） |
| [procedures/support-tickets.docx](product/procedures/support-tickets.docx) | 人工支持工单与复查 SOP | 当前 / 2.0 | 1088 个中文字（核心） |
| [01-order-sync-switch.md](product/01-order-sync-switch.md) | 店铺订单与发货同步设置 | 当前 / 2.0 | 998 个中文字（核心） |
| [03-sku-mapping.md](product/03-sku-mapping.md) | 订单 SKU 映射与库存规则的区别 | 当前 / 2.0 | 971 个中文字（核心） |
| [05-history-recovery.md](product/05-history-recovery.md) | 指定缺失与历史订单恢复 | 当前 / 2.0 | 1016 个中文字（核心） |
| [07-authorization-and-connection.md](product/07-authorization-and-connection.md) | 店铺授权与连接状态 | 当前 / 2.0 | 952 个中文字（核心） |
| [09-product-version.md](product/09-product-version.md) | 产品版本与知识适用性 | 当前 / 2.0 | 459 个中文字 |
| [11-approval-boundary.md](product/11-approval-boundary.md) | 恢复确认、管理员批准与方案有效期 | 当前 / 2.0 | 1004 个中文字（核心） |
| [12-unsupported-features.md](product/12-unsupported-features.md) | 当前能力边界与不可承诺事项 | 当前 / 2.0 | 693 个中文字 |
| [error-evidence.md](product/error-evidence.md) | 错误证据与当前事实判断 | 当前 / 2.0 | 925 个中文字（核心） |
| [order-deduplication.md](product/order-deduplication.md) | 订单重复投递与内容冲突 | 当前 / 2.0 | 1000 个中文字（核心） |
| [shipment-result-unknown.md](product/shipment-result-unknown.md) | 发货请求结果未知与安全核对 | 当前 / 2.0 | 984 个中文字（核心） |
| [inventory-version-conflicts.md](product/inventory-version-conflicts.md) | 库存版本、数量差异与信息不足 | 当前 / 2.0 | 918 个中文字（核心） |
| [support-access-and-roles.md](product/support-access-and-roles.md) | 支持访问范围与角色权限 | 当前 / 2.0 | 1058 个中文字（核心） |
| [14-legacy-order-sync.md](product/14-legacy-order-sync.md) | Legacy 1.0 订单同步历史资料 | 历史资料 / 1.0 | 353 个中文字 |
| [reference/error-codes.xlsx](product/reference/error-codes.xlsx) | 错误码与调查依据参考表 | 当前 / 2.0 | 48 条参考 |
| [reference/business-states.xlsx](product/reference/business-states.xlsx) | 业务对象状态参考表 | 当前 / 2.0 | 57 条参考 |
| [reference/channel-capabilities.xlsx](product/reference/channel-capabilities.xlsx) | 渠道 A / B 当前能力对照表 | 当前 / 2.0 | 12 条参考 |

## 旧来源迁移与合并

新来源内容与文件完整性确认后，删除以下重复或被迁移旧文件。保留文件名的专题已经直接扩充、修正，而不是另加同内容副本。

| 旧文件 | 当前主要来源 | 调整 |
| --- | --- | --- |
| 02-order-eligibility.md、04-order-status.md | guides/order-guide.pdf | 合并资格、流程、完成判断与异常处理 |
| 06-shipment-facts.md | guides/shipment-guide.pdf | 迁移为完整仓库与平台发货指南 |
| 07-channel-authorization.md、07-channel-errors.md | 07-authorization-and-connection.md | 合并当前授权与连接状态；错误标签索引在 error-codes.xlsx |
| 08-stock-facts.md、10-stock-rule.md | guides/inventory-guide.pdf | 合并计算、传播、刷新与能力边界 |
| 08-error-codes.md | reference/error-codes.xlsx | 迁移为分范围、带证据与下一步的参考表 |
| 13-channel-differences.md | reference/channel-capabilities.xlsx | 修正渠道 A 授权豁免，按当前同源逻辑对照 |
| 05-history-recovery.md、11-approval-boundary.md | 原文件扩充 | 修正普通用户确认与开同步管理员审批 |
| 14-legacy-order-sync.md | 原文件保留 | 显式历史 1.0，移除不能用于当前版本的旧指令 |

## 知识边界与后续工作

- 业务规则依据 simulator/services 与 backend/app 当前实现。Evaluation Case、实验脚本直改数据库或未来计划都不作为产品能力依据。当前仅有本地模拟平台、商家和仓库，不声称有真实商业渠道集成、OAuth 自动执行或生产 SLA。
- 订单、工作任务、派单、发货、库存、授权、同步、证据、恢复、等待、人工工单和角色范围均已有主来源；未发现代码已有而完全没有知识说明的主要业务能力。参考表并非穷尽任意运行时异常类或第三方错误文字。
- 真正外部平台的授权入口、渠道专属限制、真实仓库人工作业、生产时限没有当前实现与可核对材料，继续标为未支持或未知，不编造操作步骤。若后续提供真实代码或 SOP，再补对应知识。
- 当前导入器递归读取 product 下的 Markdown、PDF、DOCX、XLSX，23 份来源生成 181 个 Chunk；正文按长度切分，表格按完整记录切分，并保留概述。来源元数据沿用文件信息及相同公司、产品、版本的有效期；现有 PostgreSQL 表结构和检索流程保持不变。
- 导入会同步当前来源并清理已删除或迁移的旧记录；Legacy 1.0 保留入库，通过原有版本与有效期过滤隔离。旧 Evaluation 仍有指向旧文件的来源路径标签，正式评估前需要核对迁移映射；本轮没有修改或运行 Evaluation。
