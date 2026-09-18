# V2 验收矩阵

> 事实来源：`V2/building-plan-V2.md`。  
> 本矩阵只用于追踪，不新增或修改需求。  
> 当前状态：Phase 10 / Task 15 为 **Complete**。50 个开发案例、Eval Runner、176 次对照配置、3-case Dry Run 和预算估算保留；正式 Benchmark、Holdout 30×3 和批量外部验收按当前测试策略不运行，也不阻塞本地交付。

| ID | Building plan 需求 | Task | Phase | 最终证据 | 当前状态 |
| --- | --- | --- | --- | --- | --- |
| R01 | 中文产品问答 | 01、02、03 | 1–2 | 查询规划、澄清、三种检索模式、真实回答与引用已接通 | Complete |
| R02 | 检索、重排与引用 | 02、03、14 | 2、9 | 三模式真实运行；4 案例对照；Qwen3 Rerank 与 Claim Support 已验证 | Phase 2 slice complete；Phase 9 汇总 |
| R03 | 两个串行角色 | 05、08、14 | 4、6、9 | Customer → Support 原子切换和角色粘滞已通过；Shipment 只读 Tool/Evidence 已接入；3 条真实 LLM 调查及远端 Trace 延期 | Phase 6 local complete；external acceptance deferred |
| R04 | 订单真实数据流 | 04 | 3 | 平台源订单、真实 HTTP 事件、同事务 receipt/task、独立 Worker、两店铺隔离、唯一商家订单及受阻状态均已验证 | Complete |
| R05 | 订单调查与恢复 | 05、06、10、11 | 4–7 | 订单恢复已有 claim/lease 与 receipt reconciliation；新增 401/403/429/500/503/timeout 与错误预算安全停止 | Complete / external acceptance deferred |
| R06 | 发货真实数据流 | 07 | 6 | 真实 HTTP/Worker 案例已贯通 Merchant dispatch → Warehouse shipment → Merchant receipt/task → Platform shipment；三端独立记录一致 | Complete |
| R07 | 发货调查与恢复 | 08、09、10、11 | 6–7 | Shipment Tool/Evidence/冲突保护、批准补传、三端 Verification、response-lost reconciliation 和连接错误停止已通过；真实 LLM 调查延期 | Complete / external acceptance deferred |
| R08 | 批准与恢复 | 06、09、10 | 5–6 | Order/Shipment 使用批准、稳定 request ID、claim/lease、receipt/reconciliation；无批准不写、响应丢失、进程终止和重复恢复测试通过 | Complete |
| R09 | Evidence 与 Tool 边界 | 03、05、06、08、10、11、12 | 2、4–7 | Stock/Shipment Tool 均只读；Evidence 带来源/对象/时间/版本；错误与调用预算安全停止；写动作由普通代码控制 | Complete / external acceptance deferred |
| R10 | 库存只读调查 | 12 | 7 | 80−10−5=65 真实发布、旧 120 失败保留、等待窗口、版本差异、未知映射和无写工具均已验证 | Complete / external acceptance deferred |
| R11 | Engineer 接手与复查 | 13、15 | 8、10 | Ticket 保存完整接手材料；订单、发货和库存复查复用现有 Verification；RESOLVED 才关闭，UNRESOLVED/NEEDS_INFO 保持或重新打开；显式 read grant、API/CLI/React 和 HTML 导出已验证 | Complete |
| R12 | 公司隔离和不可信输入 | 01、04、07、11、13 | 1、3、6–8 | Order/Shipment/Stock contract 均带 scope；Ticket 跨公司、同公司其他商家和未获 grant 的 Engineer 读取均被拒绝 | Phase 8 slice complete |
| R13 | 可重复评测 | 01 持续累积、14 汇总 | 1–9 | 50 dev + 30 frozen holdout；176 次对照计划、指标分母、失败计数、价格缺失和 checksum 门禁已验证；正式付费运行当前不执行 | Preparation complete；formal benchmark deferred |
| R14 | 本机部署和可复现交付 | 01、04、15 | 1、3、10 | Compose init 可从 migration/bootstrap 启动，API 镜像隔离 Lab/Eval/Holdout/V1；测试 fixture/CI 固定不同的 primary/fallback 假模型名称并关闭 LangSmith；README、架构、Demo、环境变量和限制已更新；本地健康检查与浏览器固定流程通过 | Complete |

## Phase 状态

| Phase | 对应范围 | 状态 | 进入条件 |
| --- | --- | --- | --- |
| 0 | Source of Truth、V1 只读边界、验收矩阵、实施门禁 | Complete | 已完成 |
| 1 | Task 01：终端 Customer RAG 第一条纵向切片 | Complete | 已完成并已获确认 |
| 2 | Task 02–03：混合检索、重排、引用检查 | Complete | 已完成并已获确认 |
| 3 | Task 04：订单真实数据流 | Complete | 业务、双店铺、回归与 LangSmith 远端 Trace 均已通过 |
| 4 | Task 05：Handoff 与订单动态调查 | Implementation Complete / External Acceptance Deferred | Phase 9/最终验收补 3 条真实调查、不同工具路径、提前停止/预算行为及远端 Trace；不阻塞本地迁移 |
| 5 | Task 06：订单批准恢复与验证 | Complete | 46 项回归通过；真实 HTTP/Worker 恢复为 `verified_resolved`；重复批准仅一笔业务效果 |
| 6 | Task 07–10：发货、调查、恢复与恢复韧性 | Complete / External Acceptance Deferred | 58 项回归、Ruff、跨公司隔离和真实 response-lost 对账均通过；真实 LLM 调查/远端 Trace 留到 Phase 9 |
| 7 | Task 11–12：渠道错误与库存只读 | Complete / External Acceptance Deferred | 80 项回归、Ruff、Compose 渠道隔离与库存发布通过；真实模型/远端 Trace 留到 Phase 9 |
| 8 | Task 13：Engineer Ticket；最小 API/前端演示 | Complete / External Acceptance Deferred | 88 项回归、Ruff、React build、Ticket/API 权限和去重通过；真实模型/远端 Trace 留到 Phase 9 |
| 9 | Task 14：评估工具、Dry Run、审批后完整 Benchmark | Preparation Complete / Formal Benchmark Deferred | 3-case validate-only Dry Run 已通过；176 对照、90 Holdout 和延期外部验收保持 0 正式 runs，当前不执行且不阻塞 Phase 10 |
| 10 | Task 15：Ticket recheck、独立交付与文档 | Complete | 三类 Ticket 复查、关闭/重开、API/CLI/React、HTML 导出、Compose init、独立性和本地固定验收完成 |

## 验收记录规则

- 状态只能基于真实代码、测试、业务结果和 Trace 更新。
- 外部模型/LangSmith 验收当前不执行；本地固定测试显式关闭追踪并使用模型占位 Key，Google primary/fallback 必须使用两个不同的假模型名称。
- 固定文本、Fake Tool、假 Success 或只打开页面不能标记需求完成。
- Holdout 失败如实记录，不修改答案或期待结果来提高指标。
- 每个 Phase 结束时只更新该 Phase 已产生的真实证据。
- V1 的代码、测试、migration 和结果不计作 V2 验收证据。
