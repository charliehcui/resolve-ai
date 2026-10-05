# Safety 优化记录

## 当前授权变化后的执行阻止

- **问题（Problem）**：审批后店铺授权可能过期，而店铺版本未变化。旧订单恢复仍先创建修复回执和任务，随后才被 Worker 阻止。
- **原因（Why）**：有效的旧审批不能替代当前授权；同时，合法订单、发货和库存动作仍需能够完成。
- **修改（Change）**：订单与发货恢复在创建执行记录或提交修复之前重新读取当前授权。读取失败或未授权时阻止旧方案。
- **之前（Before）**：Baseline 19/20（95%）；Invalid Action Rejection 12/13（92.31%）；存在 1 次不安全提交。六个选定开发案例在基线中通过 5/6。
- **之后（After）**：原失败加五个回归案例通过 6/6。最终 Development 20/20（100%），Invalid Action Rejection 13/13（100%），Unsafe Effects 0。授权案例没有创建执行、回执或业务变化。
- **最终决定（Final Decision）**：保留执行前授权重读，开发阶段结束后冻结，不用 Holdout 调优。完整阶段结果见 [Benchmark 历史](../BENCHMARK_HISTORY.md)。

## 评测准备修正

Baseline 前补充了五个未覆盖的 Development 场景和五个新的 Holdout 组合。Safety Task Success 纳入 Safety Cases；SQL 副作用检查覆盖每张业务表、回执、队列及其他公司。这些准备修正不算 Agent 改善。

没有发现原 Ground Truth 冲突，Workflow / RAG 数据行与冻结 Fixture 保持不变。
