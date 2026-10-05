# Agent 与模拟器演示

本次修改限定在 V2。客户智能体（Customer Agent）的检索增强生成（RAG）流程保留。

## 当前流程

```text
用户问题 → 交接记录（Handoff）
  → 诊断（Diagnosis）：LLM 选择只读工具 → 保存证据（Evidence）→ 继续调查
  → 同一次结束响应：诊断（Diagnosis）+ 可选候选动作（Candidate Action）
  → 动作计划（Action Plan）：核对证据编号、对象范围与最新后台事实
  → 审批（Approval）：低风险由会话所属用户明确确认；中高风险由管理员批准
  → 执行（Execution）：重查版本、取得执行锁、调用实际写接口、保存回执
  → 验证（Verification）：重新读取实际状态，成功后才标记 verified_resolved
```

每轮调查使用新读取的证据（Evidence），历史记录继续保存在原支持案例（Support Case）中。创建计划、审批、执行与验证都不额外调用大语言模型（LLM）。聊天确认只绑定最近回复展示的具体动作计划（Action Plan）；`yes`、`确认执行`可以确认，`ok`、`sounds reasonable`、`可以考虑`不会批准。

## 文件与函数

| 文件 | 职责或变化 |
|---|---|
| `backend/app/support_diagnosis.py` | 从 `support_agent.py` 重命名；同一响应返回诊断（Diagnosis）及 `recommended_action`。 |
| `backend/app/support_action_plans.py` | 从 `support_action_proposals.py` 重命名；`create_action_plan()` 核对候选动作（Candidate Action）并刷新事实。 |
| `backend/app/support_action_approvals.py` | `decide_action_plan()` 按代码规定的风险与对象范围审批；解析绑定计划的明确聊天确认。 |
| `backend/app/support_action_execution.py` | 保留订单与发货执行；新增 `execute_background_action()`，处理库存发布与已有任务重试。 |
| `backend/app/support_action_verification.py` | 保留原有确定性检查（Deterministic Checks）；补齐库存、任务和来源版本检查。 |
| `backend/app/support_action_registry.py` | 简单动作注册表（Action Registry），规定风险、审批、构建器、执行器及验证器。 |
| `backend/app/support_action_store.py` | 各层共用的持久化（Persistence）、对象范围查询与审计记录。 |
| `backend/app/support_workflow.py` | 调查编排（Orchestration）在诊断结束后创建计划；不会自行批准。 |
| `backend/app/conversations.py`、`api.py`、`cli.py` | 接入新的计划与确认流程，修复重构后遗留的失效导入。 |
| `backend/app/support_tools.py` | 增加只读 `GetWorkerTask`；原 `GetStockStatus` 继续核对三方库存。 |
| `simulator/lab/scenarios.py`、`bootstrap.py`、`cli.py` | 新增独立演示店铺、可重复的代表场景和场景命令。 |
| `simulator/services/merchant.py`、`worker.py`、`common.py` | 最小任务状态、一次性故障注入（Fault Injection）、库存刷新和任务重试接口。 |
| `simulator/services/warehouse.py` | 实验控制（Lab Control）可指定库存观测时间，便于复现传播窗口之外的差异。 |
| `infra/migrations/009_action_plans.sql` | 扩展已有数据库表（Database Tables）、任务版本与新增修复回执。 |
| `tests/test_agent_scenarios.py` | 真实模拟器接口与数据库（Database）的端到端测试（End-to-End Tests）。 |

`propose_order_recovery()` → `build_order_action_plan()`；`propose_shipment_recovery()` → `build_shipment_action_plan()`；`decide_recovery_action()` → `decide_action_plan()`；`get_action_proposal()` → `get_action_plan()`。旧数据库表名 `support.action_proposals` 保留，旧动作类型 `recover_order` / `recover_shipment` 可作为兼容别名读取。旧重复实现不保留；原工作区已删除的 `actions.py` / `verification.py` 继续由上述专职模块承接。

## 支持的场景

| 场景 | 实际模拟事实 | 处理结果 |
|---|---|---|
| `order_sync_failure` | 已付款订单存在；商家任务一次性处理失败；连接与同步开关健康。 | `retry_order_sync`，用户确认后恢复唯一商家订单并重新核验。 |
| `shipment_sync_failure` | 仓库与商家保留同一出库事实；一次性发送失败；平台尚未收到。 | `resend_shipment`，用户确认后补传既有发货，不产生第二次出库。 |
| `inventory_mismatch` | 平台仍为 120；仓库更新后规则计算值为 65，且已超出传播窗口。 | `refresh_inventory`，用户确认后发布现有规则计算值，再核对数量和来源版本。 |
| `worker_task_stuck` | 已有订单任务停在过期的 processing 状态；当前来源与店铺允许处理。 | `retry_failed_task`，用户确认后重新排队同一个任务，再核对任务完成及唯一订单。 |
| `shop_authorization_expired` | 当前连接为 `auth_expired`，历史任务记录真实授权错误。 | `request_reauthorization` → `user_action_required`；无执行器、无 OAuth 修复写入。 |
| `third_party_outage` | 当前渠道不可用，处理记录为真实 503 失败。 | 不创建写动作；等待外部恢复，持续失败可升级人工。 |
| `rate_limit` | 当前渠道限流，处理记录保留 429 错误。 | 不立即重试；返回等待建议，恢复后重新调查。 |
| `missing_sku_mapping` | 已付款订单使用未映射商品；任务被阻止。 | 缺少可信映射，保留证据（Evidence）并创建工程师工单（Engineer Ticket）。 |
| `shipment_response_lost` | 原有实验场景：平台受理发货后响应丢失。 | 保留原回执核对（Receipt Reconciliation）和幂等行为（Idempotency）。 |

四种低风险操作都能在有效确认后自动完成后台修复。没有任何未确认的业务写入。修改店铺全局同步开关属于更广范围的变更，提升为中风险并要求管理员。

## 复现

在仓库根目录运行现有容器（Docker）启动流程：

```powershell
docker compose up -d --build db init merchant platform warehouse worker api
python -m simulator.lab.cli seed --scenario order_sync_failure
```

将 `--scenario` 改成表中的场景名称即可。新场景使用不同的 `shop-demo-*` 店铺，并生成唯一订单编号，避免相互影响。初始化（Bootstrap）已创建所需映射与库存规则。命令输出包含 `message`，可复制到支持会话；客户智能体（Customer Agent）交接后，继续发送该问题让支持智能体（Support Agent）调查。

使用会话所属的 `staff-a` 身份也能确认低风险计划。确认前检查回复展示的对象与操作，再发送 `确认执行`，或调用 `/api/v1/actions/{action_id}/decision` 并提交 `{"decision":"approve"}`。过期计划需要重新调查；来源改变会阻止执行。

命令行（CLI）保留人工创建计划的调试入口：

```powershell
python -m backend.app.cli action plan CASE_ID --type refresh_inventory
python -m backend.app.cli action decide ACTION_ID --decision approve
```

令牌（Token）使用现有 `RESOLVEAI_TOKEN` 设置。旧 `action propose` 命令暂时作为兼容别名保留，正常智能体（Agent）演示不需要手工选择动作类型。

## 验证与限制

测试（Tests）使用真实 PostgreSQL 和模拟器的 FastAPI 接口。模型响应与 HTTP 传输受控，实际状态变化、任务处理、证据保存、审批及回执核对均运行项目代码。覆盖四种修复、用户操作、等待和人工升级；另外覆盖权限注入、伪造证据、会话范围、模糊确认、审批过期、来源变化、重复执行、写入后等待验证和丢失回执后的恢复。高风险审批通过替换现有操作的测试策略验证，不新增退款或删除账户的虚假接口。

本次完整测试（Tests）：131 项通过。变更文件的静态检查（Lint）通过，前端构建（Frontend Build）通过，差异格式检查（Diff Check）通过。全仓静态检查（Lint）仍有 4 条既有问题，位于未修改的 `citations.py` 和 `config.py`，已与原版本对照确认；没有关闭规则或改动无关代码。测试（Tests）还有一条上游 Starlette 弃用提示，未影响结果。

不实现真实 OAuth 界面、外部平台故障恢复、任意任务调度、自动修改 SKU 映射、退款或删除账户。任务重试限定为现有订单处理任务；库存刷新只按可信现有规则发布计算值，不改动实物库存、安全库存或映射。真实模型（Live LLM）的动作选择质量仍需使用实际凭据另行演示，离线测试不证明模型在所有表达下都能选对动作。
