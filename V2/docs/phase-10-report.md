# Phase 10 / Task 15 报告

状态：**Complete**  
日期：2026-09-18

## 1. 完成内容

- 在现有 Ticket、Evidence、Action 和 Verification 模块上新增 Engineer Ticket 复查，没有建立第二套工作流。
- 订单复查复用订单事件、SKU、数量、金额、唯一订单和任务状态检查；发货复查复用 Warehouse/Merchant/Platform 三端一致性和一次出库检查；库存复查复用现有公式、版本和时间窗口检查。
- 每次复查持久化为 `RESOLVED`、`UNRESOLVED` 或 `NEEDS_INFO`。只有 `RESOLVED` 关闭 Ticket；失败会保持或重新打开；缺少 case/shop/order/SKU/业务目标时保持打开。
- API 新增 recheck 和 HTML export；CLI 新增 `ticket recheck` 与 `ticket export`；React 工程师视图新增复查按钮和 open/closed 展示。
- Compose 新增一次性 `init` 服务，从 migration 执行到演示 bootstrap。修复 API Docker 构建上下文的 prompt 排除问题，并补入既有 `services.common` 运行依赖。
- README、架构、Demo、环境变量、限制、版本和验收矩阵已更新。Phase 9 的正式付费运行保留为延期项目，不阻塞交付。

## 2. Ticket 复查数据流

```text
Assigned Engineer
  → explicit ticket_read_grant check
  → Ticket category + business_target
  → existing read-only Tools
  → existing deterministic Verification
  → save ticket_rechecks row + Evidence IDs
  → RESOLVED: close
     UNRESOLVED: keep/reopen
     NEEDS_INFO: keep open
```

LLM 不参与复查成功、Ticket 关闭、权限或业务结果判定。

## 3. 主要文件

- `db/007_ticket_rechecks.sql`
- `app/tickets.py`
- `app/verify.py`
- `app/api.py`
- `app/cli.py`
- `app/report.py`
- `frontend/src/main.jsx`
- `Dockerfile.init`、`Dockerfile.api`、`compose.yaml`
- `tests/test_tickets.py`、`tests/test_api.py`、`tests/test_delivery.py`
- `docs/architecture.md`、`docs/demo.md`

## 4. 最终本地验证

- Phase 10 Ticket/API/交付定向测试：15 passed。
- Google 临时 503 fallback 定向测试：1 passed；测试 fixture 与 CI 分别固定不同的 primary/fallback 假模型名称。
- 完整 Pytest regression：101 passed，1 个第三方 Starlette/AnyIO deprecation warning。
- Ruff：passed。
- React production build：passed。
- Playwright E2E：1 passed；浏览器完成直接人工 Ticket → Engineer read grant → recheck → `NEEDS_INFO` 保持 open。
- Docker Compose config：passed。
- `init` 与 `api` 镜像：build passed。
- `init` migration/bootstrap：exit 0。
- Platform、Merchant、Warehouse、Worker、API：本地启动通过；`GET /api/v1/health` 返回 200。
- Compose 中 init、Platform、Merchant、Warehouse、Worker 与 API 的 `LANGSMITH_TRACING` 运行时检查均为 `false`。
- API 镜像隔离检查：不存在 `/app/lab`、`/app/evals` 或 `/app/V1`。
- V2 运行代码、Dockerfile、Compose 和 pyproject 的 V1 依赖检查：passed。
- V1 工作区修改检查：0 个文件；V1 保持只读。
- 常见 Groq、Google、LangSmith 与 OpenAI Secret 形态扫描：0 matches；`.env`、`.local`、虚拟环境和构建产物不纳入仓库内容扫描。

首次完整回归曾因主 Google 与 fallback 使用相同占位名称导致一项 fallback 测试失败。现已在 `tests/conftest.py` 和 CI 配置中永久改为 `google-primary-test-only` 与 `google-fallback-test-only`，定向用例和完整回归均通过。该测试全程使用 fake model，没有外部调用。

## 5. 外部调用

- Groq：0 次。
- Google Chat / Embedding：0 次。
- LangSmith Trace 上传：0 次，`LANGSMITH_TRACING=false`。
- Phase 9 正式 Benchmark：0 runs。
- Holdout 30×3：0 runs。

## 6. 已知限制

- 未验证真实模型质量、批量成本或远端 Trace；现有 Dataset/Eval Runner 只作为保留基础。
- 不连接真实电商平台、物流或外部工单系统。
- 无 Ticket 认领、重新分配和值班系统；无匹配规则时保持未分配。
- 一般咨询或缺少业务标识符的 Ticket 只能返回 `NEEDS_INFO`，不会自动关闭。
- Compose init 重跑会刷新本地演示 token；以最新 `.local/test_tokens.json` 为准。
- API 镜像包含本地 Reranker 的 Torch/Transformers，首次 Linux 构建体积较大。

## 7. 结论

Phase 10 / Task 15 的本地功能与迁移已完成。V2 可以独立执行 migration/bootstrap、启动业务服务和 API、展示 Ticket 复查/关闭，并且运行时不依赖 V1。真实模型质量评估和付费 Benchmark 仍按当前决定保持暂停。
