# V2 验收矩阵

> 事实来源：`V2/building-plan-V2.md`。  
> 本矩阵只用于追踪，不新增或修改需求。  
> 当前状态：Phase 4 / Task 05 为 **Implementation Complete / External Acceptance Deferred**；未完成的 3 条真实调查与远端 Trace 集中到 Phase 9/最终验收。Phase 5 / Task 06 已完成订单 Proposal → Approval → Execution → Verification 真实纵向切片。

| ID | Building plan 需求 | Task | Phase | 最终证据 | 当前状态 |
| --- | --- | --- | --- | --- | --- |
| R01 | 中文产品问答 | 01、02、03 | 1–2 | 查询规划、澄清、三种检索模式、真实回答与引用已接通 | Complete |
| R02 | 检索、重排与引用 | 02、03、14 | 2、9 | 三模式真实运行；4 案例对照；Qwen3 Rerank 与 Claim Support 已验证 | Phase 2 slice complete；Phase 9 汇总 |
| R03 | 两个串行角色 | 05、08、14 | 4、6、9 | Customer → Support 原子切换和角色粘滞已通过；3 条完整真实调查及远端 Trace 明确延期 | Phase 4 implementation complete；external acceptance deferred |
| R04 | 订单真实数据流 | 04 | 3 | 平台源订单、真实 HTTP 事件、同事务 receipt/task、独立 Worker、两店铺隔离、唯一商家订单及受阻状态均已验证 | Complete |
| R05 | 订单调查与恢复 | 05、06、10、11 | 4–7 | Handoff、只读 HTTP Tool、Evidence、预算保护、订单恢复与独立验证已实现；复杂恢复韧性留到 Phase 6 | Phase 5 slice complete；Phase 4 external acceptance deferred |
| R06 | 发货真实数据流 | 07 | 6 | 商家送仓、仓库接收/发货、三端结果 | Not started |
| R07 | 发货调查与恢复 | 08、09、10、11 | 6–7 | 不同失败步骤、补传、三端一致性 | Not started |
| R08 | 批准与恢复 | 06、09、10 | 5–6 | 订单拒绝、过期、版本变化、稳定幂等键、execution claim/lease、receipt 与验证已通过；完整崩溃/并发恢复留到 Phase 6 | Phase 5 slice complete |
| R09 | Evidence 与 Tool 边界 | 03、05、06、08、10、11、12 | 2、4–7 | Tool 白名单、scope、并行与预算；写入步骤关联 Evidence/receipt/request ID，成功由独立 Verification 决定 | Phase 5 slice complete |
| R10 | 库存只读调查 | 12 | 7 | 正常差异、等待窗口、真实推送失败对照 | Not started |
| R11 | Engineer 接手与复查 | 13、15 | 8、10 | Ticket 内容、身份限制、recheck 关闭/保持打开 | Not started |
| R12 | 公司隔离和不可信输入 | 01、04、07、11、13 | 1、3、6–8 | 检索及订单查询执行公司范围；业务服务令牌、跨公司 404 和冲突输入测试通过 | Phase 3 slice complete；后续入口继续 |
| R13 | 可重复评测 | 01 持续累积、14 汇总 | 1–9 | Dev 扩展到 10 案例；4 案例三模式对照；运行与 Trace 可追踪 | Phase 2 slice complete；持续累积 |
| R14 | 本机部署和可复现交付 | 01、04、15 | 1、3、10 | 锁定依赖、Compose 业务服务、Lab CLI、README 和 Phase 报告 | Phase 3 slice complete；Phase 9–10 继续 |

## Phase 状态

| Phase | 对应范围 | 状态 | 进入条件 |
| --- | --- | --- | --- |
| 0 | Source of Truth、V1 只读边界、验收矩阵、实施门禁 | Complete | 已完成 |
| 1 | Task 01：终端 Customer RAG 第一条纵向切片 | Complete | 已完成并已获确认 |
| 2 | Task 02–03：混合检索、重排、引用检查 | Complete | 已完成并已获确认 |
| 3 | Task 04：订单真实数据流 | Complete | 业务、双店铺、回归与 LangSmith 远端 Trace 均已通过 |
| 4 | Task 05：Handoff 与订单动态调查 | Implementation Complete / External Acceptance Deferred | Phase 9/最终验收补 3 条真实调查、不同工具路径、提前停止/预算行为及远端 Trace；不阻塞本地迁移 |
| 5 | Task 06：订单批准恢复与验证 | Complete | 46 项回归通过；真实 HTTP/Worker 恢复为 `verified_resolved`；重复批准仅一笔业务效果 |
| 6 | Task 07–10：发货、调查、恢复与恢复韧性 | Not started | Phase 5 完成并确认 |
| 7 | Task 11–12：渠道错误与库存只读 | Not started | Phase 6 完成并确认 |
| 8 | Task 13：Engineer Ticket；最小 API/前端演示 | Not started | Phase 7 完成并确认 |
| 9 | Task 14：评估工具、Dry Run、审批后完整 Benchmark | Not started | Phase 8 完成并确认；完整 Benchmark 另需确认 |
| 10 | Task 15：Ticket recheck、独立交付与文档 | Not started | Phase 9 完成并确认 |

## 验收记录规则

- 状态只能基于真实代码、测试、业务结果和 Trace 更新。
- 外部模型/LangSmith 出现明确 429/503 时不持续重试；保存本地运行元数据并标记 `External Acceptance Deferred`，远端验收集中到 Phase 9/最终验收。
- 固定文本、Fake Tool、假 Success 或只打开页面不能标记需求完成。
- Holdout 失败如实记录，不修改答案或期待结果来提高指标。
- 每个 Phase 结束时只更新该 Phase 已产生的真实证据。
- V1 的代码、测试、migration 和结果不计作 V2 验收证据。
