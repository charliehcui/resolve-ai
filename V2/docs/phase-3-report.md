# Phase 3 / Task 04 完成报告

> 完成日期：2026-09-17  
> 状态：完整通过。  
> 事实来源：`V2/building-plan-V2.md`。

## 1. 完成内容

- 建立 Platform、Merchant、共享 HTTP Contract、独立 Worker 和 Lab CLI。
- 新增平台源订单、事件发送记录、商家接收记录、后台任务、店铺、SKU 映射和商家订单数据模型。
- 平台先持久化源订单，再通过真实 HTTP 请求发送事件；Merchant 在同一事务中写入 receipt 与 pending task，成功响应不会早于任务落库。
- Worker 从 PostgreSQL 领取任务，根据真实店铺同步开关、渠道连接状态、付款状态和 SKU 映射决定完成或阻止；查询接口不会临时拼出“已同步”。
- 使用唯一约束和 payload hash 实现重复保护：相同订单复用稳定 event ID，相同事件重复投递只产生一笔商家订单，内容变化返回 409。
- 业务 Bearer token 从服务端映射 company；内部事件和 Lab 控制分别使用本地 service token。跨公司任务查询返回 404。
- Docker Compose 已真实运行 `platform`、`merchant`、`worker` 与 PostgreSQL；Docker build context 排除 `.env`、`.local` 和测试缓存，业务容器只注入数据库与 tracing 配置，不注入 Groq/Google Key。
- 增加 GitHub Actions 行为测试入口、Phase 3 README、版本记录和验收矩阵。

本阶段没有实现 Support Agent、Handoff 角色切换、调查 Tools、Google Tool Calling、Action、Approval、Ticket、发货或 React UI。

## 2. 主要文件

- 数据库：`db/002_orders.sql`、`app/db.py`
- HTTP 服务与 Worker：`services/common.py`、`services/platform.py`、`services/merchant.py`、`services/worker.py`
- 场景入口：`lab/bootstrap.py`、`lab/scenarios.py`、`lab/cli.py`
- 运行环境：`Dockerfile`、`.dockerignore`、`compose.yaml`、`services/requirements.txt`
- 测试与 CI：`tests/test_orders.py`、`tests/test_access.py`、`tests/conftest.py`、`.github/workflows/checks.yml`
- 交付说明：`README.md`、`docs/versions.md`、`docs/acceptance-matrix.md`

## 3. 当前真实数据流

```text
Lab CLI + 本地用户 token
  → Platform 校验用户并从服务器端确定 company
  → PostgreSQL 保存或复用平台源订单与稳定 event ID
  → Platform 通过容器网络 HTTP POST 订单事件
  → Merchant 校验 service token
  → 同一事务保存 event receipt + pending task
  → 独立 Worker 使用 FOR UPDATE SKIP LOCKED 领取任务
  → 普通代码读取 shop / connection / payment / SKU mapping
      ├─ 条件满足：唯一写入 merchant order，task=completed
      └─ 条件不满足：task=blocked/failed + error_code，不写 merchant order
  → Lab 分别查询 Platform order、Merchant task 和 Merchant order
```

实际验证结果：

- `O-1001`：HTTP 200，任务完成，平台与商家订单的 SKU、数量和金额一致。
- `O-1002`：同步关闭后 receipt/task 仍真实存在，任务为 `ORDER_SYNC_DISABLED`，商家订单不存在。
- `O-1003`：使用不存在的 SKU 映射，任务为 `SKU_MAPPING_MISSING`，商家订单不存在。
- `O-1004`：停止 Merchant 后 Platform 保存源订单和一条 `ConnectError` delivery，receipt/task 都是 0；Merchant 恢复后重试同一订单，复用原 event ID 并最终只生成一笔商家订单。
- 再次发送 `O-1001` 相同内容返回 `duplicate_order=true` 和 `merchant duplicate=true`；改变数量返回 409。
- 两店铺隔离复核：`shop-a` 与 `shop-b` 对同一个外部订单号生成不同 event ID。只关闭 `shop-a` 同步后，`shop-a` 留下 `ORDER_SYNC_DISABLED` 且没有商家订单；`shop-b` 同时正常完成并生成一笔商家订单。复核后已恢复 `shop-a` 同步。

这些结果来自服务状态、真实 HTTP 调用、事务和数据库约束，不来自固定 scenario 结果。

## 4. 测试与验证结果

- `python -m pytest`：28 项通过。
- `python -m ruff check app lab services tests`：通过。
- 测试从隔离的空 PostgreSQL 数据库顺序运行 `001_support.sql` 与 `002_orders.sql`；Phase 1–2 回归继续通过。
- 自动测试覆盖 receipt/task 原子落库、重复事件、内容冲突、同步关闭、SKU 处理失败、Worker 重启恢复 pending、跨公司隔离、服务凭据和平台订单去重。
- `docker compose up -d --build merchant platform worker`：三个业务容器均成功运行，数据库健康。
- 最终镜像重建后，Platform 与 Merchant health 都返回 `ok`；`O-1005` 再次完整通过源订单、HTTP、receipt/task、Worker 和商家订单，证明最小业务容器不依赖模型 Key。
- 唯一警告来自 Starlette TestClient 使用 AnyIO 兼容别名的第三方 DeprecationWarning，不影响断言结果。

## 5. LangSmith Trace

- Platform delivery、Merchant receipt 和 Worker task 已使用 LangSmith integration 建立真实 tool trace 边界；本地业务记录保存可关联的 trace ID。
- 配置复核确认当前 Key 在 GCP US endpoint 认证成功；EU、APAC 和 AWS US endpoint 不匹配。V2 项目名已从旧的 `resolveai-v1` 修正为 `resolveai-v2`。
- 当前 Key 不配置 Workspace ID 也能读取项目，因此该 Key 不需要额外 workspace 选择；Compose 仍显式传递可选 endpoint/workspace 配置，便于配置变化时保持主机与容器一致。
- 重新运行两条真实订单流程后，Platform、Merchant 和 Worker 三类 Trace 都保存了非空 trace ID，且可由 LangSmith API 在 `resolveai-v2` 项目中远端查询。
- `.env` 继续被 Git 忽略，代码、日志和报告没有输出 API Key、Bearer token、service token 或 Authorization Header。

## 6. 模型使用

Phase 3 没有调用 Groq、Google Chat、Embedding 或 Reranker。订单接收、权限、租户范围、幂等、处理结果和错误状态全部由普通代码与数据库决定。本阶段也没有提前实现 Google Tool Calling 或 Parallel Tool Calling。

Phase 2 的 `vector_only` 仍只是小型开发集默认；Phase 3 没有增加新检索结论，正式比较保留到 Phase 9。

## 7. 已知问题与边界

- Merchant 不可用时会持久化失败 delivery，但当前没有自动重试调度；手工重复同一订单可安全复用 event ID。完整 response-lost、worker crash 和并发恢复属于 Phase 6。
- 同步关闭或 SKU 映射缺失产生的 blocked task 不会因配置变化自动恢复；受控 Action、Approval、Execution 和 Verification 属于后续阶段。
- 只覆盖已付款单订单及必要拒绝状态，不做拆单、促销分摊、真实平台连接或发货。
- 当前 HTTP API 是业务模拟器 Contract，不是 Phase 8 的完整产品 API/React UI。
- `V1/.gitignore` 的 SHA-256 仍与 Phase 0 基线一致；Phase 3 未修改、删除、移动或依赖 V1 文件。`building-plan-V2.md` 的 SHA-256 也保持 Phase 0 基线值。

## 8. 下一阶段

Phase 3 已满足进入条件。根据用户本轮确认，下一步只实施 Phase 4 / Task 05：结构化 Handoff、Conversation 角色切换、Support Agent 动态只读调查、Evidence 和独立检查器。
