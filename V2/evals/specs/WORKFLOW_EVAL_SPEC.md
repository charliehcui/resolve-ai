# Workflow Evaluation Dataset Spec

- 总数：50 Workflow Cases；原有 34，新增 16，无删除/合并。
- Development / Regression：40（17 Development + 23 Regression）。
- 全新 Holdout：10。所有旧 Holdout 都是 Regression，真正 Holdout 只含下列 10 个 ID。
- Canonical Workflow Dataset SHA256：`01692ea2bc786b5f21da8c75d78e18eef9d3409fa79462c09ee9c533c6e424b1`。
- Fixture SHA256：`1ec1f234907299417035d520d92b32bb0c2876e6de10ead65a910202e5b1b9c8`。算法和冻结检查见 GROUND_TRUTH_REVIEW.md。

## 覆盖范围

| 编号 | 场景 | 代表 Case ID |
|---|---|---|
| 01 | 正常调查并解决 | flow-order |
| 02 | 缺少用户信息 | flow-dev50-missing-shop |
| 03 | 缺少订单/SKU/发货查询标识 | flow-dev50-missing-shop |
| 04 | 多步调查 | flow-dev50-stock-quantity |
| 05 | 工具返回部分信息 | flow-dev50-timeout |
| 06 | 读取工具失败 | flow-holdout50-outage-read-refusal |
| 07 | 临时错误 | flow-dev50-timeout |
| 08 | Unknown 状态 | flow-holdout50-outage-read-refusal |
| 09 | 多个可能原因但只一个有证据 | flow-dev50-cancelled |
| 10 | 根因证据不足 | flow-holdout50-mapping-stock |
| 11 | 证据齐全后停止 | flow-dev50-cancelled |
| 12 | 无关工具风险 | flow-dev50-missing-shop |
| 13 | 重复工具风险 | flow-holdout50-stock-active |
| 14 | 应该转人工 | flow-holdout50-mapping-stock |
| 15 | 不应该转人工 | flow-dev50-missing-shop |
| 16 | 用户拒绝人工 | flow-holdout50-outage-read-refusal |
| 17 | 用户条件式要求人工 | flow-holdout50-mapping-stock |
| 18 | 用户拒绝重试 | flow-holdout50-outage-read-refusal |
| 19 | 合法恢复动作 | flow-dev50-forbidden |
| 20 | 不满足恢复条件 | flow-dev50-cancelled |
| 21 | 多个合法动作 | flow-order |
| 22 | Worker/processing failure | flow-holdout50-shipment-worker |
| 23 | Shipment 状态冲突 | flow-holdout30-shipment-conflict |
| 24 | Inventory 状态冲突 | flow-dev50-stock-quantity |
| 25 | SKU mapping 问题 | flow-holdout50-mapping-stock |
| 26 | Merchant connection/authorization | flow-dev50-forbidden |
| 27 | Sync 问题 | flow-dev50-shipment-sync-off |
| 28 | Platform 与 warehouse 不一致 | flow-dev50-stock-quantity |
| 29 | 无依据原因猜测 | flow-dev50-missing-shop |
| 30 | 不存在的自动处理承诺 | flow-dev50-forbidden |

## Failure Categories

Wrong Tool, Missing Tool, Unnecessary Tool, Repeated Tool, Wrong Argument, Wrong Diagnosis, Wrong Handoff, Premature Stop, Incomplete Action, Unsupported Claim, Invalid Action, Provider Error, Evaluation Error。同一结果允许多个类别。

## Holdout Case IDs

| Case ID | 场景 |
|---|---|
| flow-holdout50-auth-completed | 订单已导入但当前授权过期，分别确认，不重试订单。 |
| flow-holdout50-mapping-stock | 订单 SKU 映射与库存规则分别缺失，核实两者后人工。 |
| flow-holdout50-shipment-worker | 订单 Worker 已完成但发货转发失败，只恢复发货。 |
| flow-holdout50-outage-read-refusal | 渠道不可用且处理读取失败，用户拒绝人工与重试，保留未知。 |
| flow-holdout50-stock-auth | 库存确有差异但授权过期，先请求授权，不刷新。 |
| flow-holdout50-conflict-no-human | 历史任务完成但当前订单冲突，用户拒绝人工与覆盖。 |
| flow-holdout50-auth-restored-sync-off | 历史授权错误已恢复，但当前同步关闭，不能误判根因。 |
| flow-holdout50-shipment-read-unknown | 仓库已出库、平台无记录、中间读取失败，不能猜根因或重发。 |
| flow-holdout50-receipt-no-retry | 来源存在但接收为空、设置健康，用户拒绝重试，条件满足后人工。 |
| flow-holdout50-stock-active | 库存版本/数量有差异且发布仍 processing，按用户条件等待。 |
