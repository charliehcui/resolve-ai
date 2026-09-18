# Phase 8 / Task 13 报告

状态：**Complete / External Acceptance Deferred**  
日期：2026-09-18

## 1. 完成内容

- 新增 Engineer Ticket migration、确定性创建策略、同会话未关闭 Ticket 唯一约束、公司 A → `engineer-a` 的最小分配规则，以及无匹配规则时的未分配队列。
- Ticket 关联并展示 Customer Handoff、会话时间线和 Evidence，并保存已尝试操作、确认事实、可能原因、已排除原因、未知项、当前结果和下一步建议。事实和可能原因都引用已有 Evidence；没有订单、店铺或 SKU 时不会编造标识。
- 用户明确请求人工可直接建 Ticket；Support case 只有在 `pending_human`、调查/错误预算到达或失败 Action 未解决时才可自动建 Ticket。重复请求返回同一未关闭 Ticket。
- Engineer 只能通过显式 `ticket_read_grants` 读取分配给自己的 Ticket；普通商家只能读取自己会话里的 Ticket，跨公司或同公司其他用户均被拒绝。
- 新增 `/api/v1` FastAPI、OpenAPI contract 和最小 React/Vite 演示页。页面展示会话、当前 Agent、Citation、Evidence、Proposal、Approval、Verification 和 Engineer Ticket，不包含旧 customer selector 或手工 Agent toggle。
- CLI、API 和 React 统一调用 `backend.app.conversations.process_conversation_message`、Action 函数和 Ticket 函数；前端不复制 Graph、授权、批准或 Verification 判断。
- 未实现 Phase 10 的 Ticket recheck/关闭流程。

## 2. 真实数据流

```text
商家 CLI / API / React
  → Bearer token → company/user scope
  → process_message（与 CLI 共用）
  → Customer Graph 或已切换的 Support Graph
  → Evidence / Action / Verification 持久化
  → 普通代码判断 user_requested / pending_human / budget_reached
  → create_ticket policy check
  → 同会话 active Ticket 去重
  → assignment rule
  → ticket + explicit read grant
  → 被授权 Engineer 通过 API/CLI 读取 Handoff、timeline、Evidence、事实、未知项和下一步
```

Support Agent 的模型可选择工具仍全部只读；`create_ticket` 是受控普通代码工具，没有加入 LLM Tool schemas。Engineer Ticket 不承担 Customer → Support 的 Handoff 通信职责。

## 3. 主要文件

- `db/006_tickets.sql`：Ticket、分配规则、最小读取授权和未关闭 Ticket 唯一约束。
- `app/tickets.py`：资格检查、摘要组装、分配、读取授权和去重。
- `app/conversations.py`：CLI/API/UI 共用的单轮会话业务入口。
- `app/api.py`：最小 versioned API 与 OpenAPI contract。
- `app/graph.py`、`app/support.py`、`app/tools.py`：Support 安全停止后的受控 Ticket 创建及 Ticket ID 返回；写工具不暴露给模型。
- `app/cli.py`：复用共享会话逻辑并增加 Ticket create/show/list。
- `frontend/`：无额外状态框架的 React/Vite 演示页。
- `tests/test_tickets.py`、`tests/test_api.py`：Ticket 策略、去重、分配、租户/工程师隔离和 API contract。

## 4. 测试结果

- Ticket/API/Handoff 定向测试：19 passed。
- 完整 pytest：88 passed。
- Ruff：passed。
- React production build：passed。
- Playwright 前端 E2E：1 passed（9.6 秒）。浏览器实际创建商家会话，Groq 将真实订单问题路由为 SUPPORT，页面创建 Engineer Ticket，`engineer-a` 随后成功读取被分配的 Ticket。
- migration 每个测试会话均从空的隔离 PostgreSQL 数据库运行，包括 `006_tickets.sql`。

## 5. LangSmith Trace 与模型

Phase 8 初次交付没有调用 Groq/Google。进入 Phase 9 前补充的最小浏览器 E2E 使用了一次真实 Groq Customer Query Plan，使会话从 CUSTOMER 切换到 SUPPORT；Ticket eligibility、assignment、authorization 和 deduplication 仍由确定性普通代码完成。测试显式设置 `LANGSMITH_TRACING=false`，没有上传远端 Trace；真实 Google Support 调查与远端 LangSmith Trace 按既定规则保留到 Phase 9，状态为 **External Acceptance Deferred**，没有伪造 Trace 或成功结果。

## 6. 已知问题

- 无 assignment rule 的 Ticket 保持未分配；本阶段没有实现队列认领、重新分配或值班系统。
- Ticket 关闭和重新检查属于 Phase 10，当前未实现。
- React 页面是作品集演示入口，不包含复杂设计系统、路由或状态管理框架。
- 真实 LLM 调查和远端 LangSmith Trace 尚待 Phase 9 统一验收。

## 7. 下一阶段

Phase 9 / Task 14 将建立 Dataset、Eval Runner 和 Dry Run，先统计运行次数、预计 tokens、费用和时间；未经确认不运行完整 Benchmark。
