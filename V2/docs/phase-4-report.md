# Phase 4 / Task 05 完成报告

> 完成日期：2026-09-17  
> 状态：Implementation Complete / External Acceptance Deferred。  
> 事实来源：`V2/building-plan-V2.md`。

## 1. 完成内容

- 结构化 Handoff 保存客户问题、Customer RAG 回答与引用、已尝试步骤、已知标识符和缺失字段，并原子切换 `conversation.active_role=SUPPORT`。
- Support Agent 后续消息保持 SUPPORT，不重新运行 Customer RAG，也没有 Internal RAG。
- 建立 `GetOrder`、`GetShopStatus`、`GetProcessRecords` 和 `CheckConnection` 四个只读 HTTP Tool，以及普通代码控制的权限、输入、scope、并行条件、重复调用、时间与工具预算检查。
- 每次 Tool 返回均保存不可变 Evidence；不存在、空结果、无权限、服务不可用和普通错误保持不同状态。
- Google 负责动态下一步规划；授权、租户范围、工具白名单、预算和停止条件由普通代码决定。
- 普通 unit、contract、graph 和 regression tests 使用 deterministic mock/stub，并在测试进程关闭 LangSmith tracing。

## 2. 真实数据流

```text
Customer conversation
  → Structured Handoff + active_role=SUPPORT
  → Support Graph 读取 Handoff/Evidence/budget
  → Google 提出 read-only tool calls 或控制决策
  → 普通代码复核 scope/permission/input/dependency/budget
  → 独立只读 Tool 并行执行；下一步依赖前一步时顺序执行
  → HTTP 查询 Platform/Merchant
  → 保存 Evidence
  → 继续调查、提前停止、补问信息或安全升级
```

## 3. 主要文件

- `db/002_support_cases.sql`
- `app/support.py`、`app/tools.py`、`app/evidence.py`、`app/graph.py`
- `services/platform.py`、`services/merchant.py`
- `prompts/support.md`
- `tests/test_handoff.py`

## 4. 测试结果

- Phase 4 实现检查时 38 项离线测试通过，Ruff 通过。
- 自动测试覆盖 Handoff 原子切换、角色粘滞、租户隔离、缺少标识符、HTTP Contract、服务不可用、独立只读工具并行、工具预算停止，以及临时 503 才允许 fallback。
- 测试不调用 Groq/Google，也不上传 LangSmith Trace。

## 5. 模型与 LangSmith

- 主模型 `gemini-3.8-flash` 和原 fallback `gemini-3.7-flash` 的 Phase 4 真实尝试均遇到临时 `503 high demand`；失败运行如实保存，没有当作空结果或成功。
- LangSmith 同期返回月度 unique traces quota 已耗尽的 429，因此新远端 Trace 无法上传。
- fallback 随后按要求改为 `gemini-3.6-flash`。关闭 LangSmith 上传后只运行一次最小真实 Support Tool Calling，模型正确返回一个 `GetOrder` 调用；没有重复请求或大规模测试。

## 6. 延期验收

以下项目没有伪装成已完成，集中到 Phase 9/最终验收：

- 3 条完整真实调查案例。
- 至少两条不同 Tool 路径。
- 真实提前停止与预算行为记录。
- 对应远端 LangSmith Trace 可查询。

Phase 4 因此标记为 **Implementation Complete / External Acceptance Deferred**。这些外部项目不再阻塞后续本地迁移，但仍保留在验收矩阵中。

## 7. 边界

- Phase 4 只调查，不执行写操作。
- Customer Agent 没有后台业务 Tool；Support Agent 没有 Customer/Internal RAG。
- Handoff 不是 Ticket；完整 Engineer Ticket 留到 Task 13。
- V1 未修改、删除、移动或成为 V2 运行依赖。
