# Reliability 优化记录

## Unknown 状态与执行租约

- **问题（Problem）**：Unknown 请求没有回执时，可能在有效租约过期之前再次提交。
- **原因（Why）**：Unknown 不能证明上次请求已经停止运行。
- **修改（Change）**：对 claimed 和 unknown 都保留有效执行租约，任何重试之前先查询原回执。
- **之前（Before）**：`recover-unknown` 失败，租约仍有效时出现第二次写入。
- **之后（After）**：该案例通过，没有提前写入；到期后以同一 Request ID 和执行记录做一次安全重试。
- **最终决定（Final Decision）**：保留租约与先查回执的边界，不用新编号绕过 Unknown。

## Verification 等待期限

- **问题**：Worker 尚未在验证等待期限内完成时，已接受的订单被永久标为失败。
- **原因**：等待期限结束不能证明业务失败，accepted 回执也不能证明成功。
- **修改**：回执任务尚未完成或暂时不可读时保持 awaiting verification；只有确认终态后才作最终失败判断。
- **之前**：Pending-worker 与 receipt-conflict 为 0/2；订单后来完成，动作仍保留失败状态。
- **之后**：两例为 2/2；Worker 恢复后验证已完成订单，源事实变化时如实返回验证失败，不产生订单效果。
- **最终决定**：保留“等待 / 未知”与确定终态的区分，不能从单次等待超时推导永久失败。

## 验证与停止

一次定向运行覆盖 3 个受影响失败和 3 个相关回归，通过 6/6；相关动作与评分单元检查通过。Baseline 后没有修改 Ground Truth，优化期间没有运行 Holdout。

最终完整 Development 为 13/13；Task / Recovery / Idempotency 100%，Duplicate / False Success 0%，Error / Timeout 0。首次 Holdout 前冻结 Agent、评分和数据集。

Holdout 的原始结果及后续标签勘误保存在 [Benchmark 历史](../BENCHMARK_HISTORY.md)，本记录没有重新评分。
