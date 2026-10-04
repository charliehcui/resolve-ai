# Workflow 固定分组

最终 50 例：Development 17 + Regression 23 = 40；全新 Holdout 10。新增 16，原有 34 全部保留。

`../data/smoke.jsonl` 的 `expected.split` 是唯一分组依据。历史 flow-holdout-*、flow-holdout30-*、flow-holdout34-* 均为 Regression。只有 flow-holdout50-* 是真正 Holdout，未运行过模型评价。

完整案例和语义标准在 ../data/smoke.jsonl / ../dataset.py。逐例审查与冻结规则在 GROUND_TRUTH_REVIEW.md；覆盖范围、Holdout IDs 和 Hash 在 WORKFLOW_EVAL_SPEC.md。

Optimization Agent 仅可运行、分析并优化 Development / Regression，不得改 Dataset、Fixture、Ground Truth 或 Hash。发现真实标签疑点需先记录依据并等待人工确认。现有 Final 会包含 Holdout，不能用作优化循环。
