+# Phase 7 / Task 11–12 完成报告

日期：2026-09-18  
状态：**Complete / External Acceptance Deferred**

## 1. 完成内容

- 多公司、多店铺、多渠道的连接状态与错误处理已覆盖 401、403、429、500、503 和 timeout。
- Order、Shipment 与 Stock Worker 都在普通代码中读取指定店铺连接状态；失败保存 HTTP 状态、错误码和 request ID，不创建虚假成功。
- 新增测试操作者专属的 `connection set/restore` 接口与 Lab CLI。接口只接受 `lab-control` 凭据；Support Agent 和普通商家没有连接写能力。
- 新增 Warehouse stock、Merchant stock rule/publish task 和 Platform stock level 三端真实数据。
- 正常库存发布按 `max(physical - reserved - safety, 0)` 计算，并经 Merchant task → Worker → Platform HTTP contract 保存版本化结果。
- Support Agent 新增唯一库存工具 `GetStockFacts`。它先读 Merchant 商品关系，再并行读取互不依赖的 Warehouse 与 Platform 事实，全程只读。
- 库存比较由普通代码执行。它区分 `consistent`、`waiting`、`difference` 和 `insufficient_information`，并检查来源版本和 30 秒传播窗口。
- Support Graph 的工具次数、调查时间、连续错误和总错误预算均可通过环境变量配置；达到上限后保留 Evidence 并安全停止。
- 冻结 30 条 Holdout Dataset，记录 SHA-256，禁止通过修改期望结果提高后续指标。

## 2. 当前真实数据流

### 渠道错误

```text
Lab operator 设置指定 company/shop 的连接状态
→ Platform 保存源订单并通过 HTTP 投递事件
→ Merchant 保存 receipt 与 pending task
→ Worker 读取该店连接状态
→ 保存 channel failure、HTTP status、error code、request ID
→ 失败店铺不创建 Merchant order；其他店铺/公司继续处理
→ 只有 Lab operator 可恢复连接
```

真实 Compose 验收中，`shop-a / O-PH7-500-0918` 保存 `CHANNEL_INTERNAL_ERROR + HTTP 500 + failure_request_id`，没有 Merchant order；同公司的 `shop-b / O-PH7-OK-0918` 随后正常完成，证明单店故障隔离。最后通过测试操作者接口恢复 `shop-a`。

### 库存

```text
Lab operator 写 Warehouse physical/reserved
→ Merchant 读取 Warehouse HTTP fact 和本店 stock rule
→ 计算 expected quantity 并创建稳定 request ID 的 publish task
→ Worker 调用 Platform HTTP contract
→ Platform 保存 quantity + source version
→ GetStockFacts 读取 mapping / Warehouse / Platform
→ 普通代码按版本与时间窗口解释结果
```

真实 Compose 验收中，Warehouse `80 - 10`、Merchant safety `5` 生成预期值 `65`；任务完成后 Platform 保存 `quantity=65, source_version=1`。

## 3. 主要文件

- `db/005_stock.sql`
- `services/common.py`
- `services/warehouse.py`
- `services/merchant.py`
- `services/platform.py`
- `services/worker.py`
- `app/stock.py`
- `app/tools.py`
- `app/evidence.py`
- `app/support.py`
- `app/graph.py`
- `app/verify.py`
- `lab/cli.py`
- `lab/scenarios.py`
- `tests/test_channels.py`
- `tests/test_stock.py`
- `evals/holdout.jsonl`
- `evals/holdout-summary.json`
- `docs/product/07-channel-errors.md`
- `docs/product/08-stock-facts.md`

## 4. 测试结果

- Phase 7 定向测试：22 项通过。
- 完整 Pytest：80 项通过，退出码 0。
- Ruff：全部通过，退出码 0。
- 测试从空的隔离数据库执行全部 migration，包括 `005_stock.sql`。
- Docker Compose 实际构建并启动 db、merchant、platform、warehouse、worker；真实渠道隔离和库存 65 流程均通过。
- 唯一警告是 Starlette TestClient 对 AnyIO 别名的上游弃用提醒，不影响结果。

## 5. LangSmith Trace 与模型

- Phase 7 没有调用 Groq 或 Google。
- 普通测试强制关闭 LangSmith；Compose 验收也显式使用 `LANGSMITH_TRACING=false`。
- 因此本 Phase 没有新增远端 Trace，这不是伪造或上传失败。
- Phase 4/6 遗留的真实 LLM 调查和远端 LangSmith Trace，以及 Phase 7 的真实模型调查，继续标记为 **External Acceptance Deferred**，集中到 Phase 9/最终验收。
- 本 Phase 的安全判断、库存计算、版本/窗口比较和预算停止全部由确定性普通代码完成，不需要模型。

## 6. 已知问题与边界

- 渠道错误来自本地可控的店铺状态和真实 Worker 处理过程，不连接真实电商平台。
- 库存仅支持一条规则、单仓映射和只读调查；没有库存修复工具。
- 传播窗口目前默认 30 秒；证据缺少映射、版本或时间时不会强行计算。
- 库存失败最终创建 Engineer Ticket 的能力属于 Phase 8，当前只返回待人工处理。
- Holdout Dataset 已冻结，但完整 Benchmark 必须等 Phase 9 Dry Run、费用和时间预估后由用户确认。

## 7. 下一阶段

Phase 8 / Task 13 将建立 Engineer Ticket、最小 versioned API 和 React 演示界面，展示 Conversation、当前 Agent Role、Evidence、Proposal、Approval、Verification 与 Ticket。未经确认不进入 Phase 8。
