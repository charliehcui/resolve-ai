# ResolveAI

企业级双 Agent 智能客服与业务执行系统。

## 项目简介

ResolveAI 面向电商商家管理软件的技术支持。用户先通过 Customer Agent 查询产品说明，需要调查真实业务状态时交给 Support Agent。系统可以定位订单未导入、发货状态不一致、库存发布异常等问题，并在用户确认或管理员批准后执行受限操作，再读取实际业务结果验证是否完成。

项目使用本地 Platform、Merchant、Warehouse 和 Worker 模拟业务链路。它展示企业级支持系统的设计与工程验证，不代表已经接入真实商业平台。

## 核心功能

- **Customer Agent**：产品问答、澄清、带引用的回答和结构化交接（Handoff）。
- **Support Agent**：动态选择只读工具，收集证据，诊断问题并提出候选动作。
- **RAG**：多格式产品资料导入、范围过滤、检索与引用验证。
- **Tool Calling**：订单、处理记录、店铺连接、发货和库存事实查询。
- **安全执行**：确认、审批、权限和当前事实检查由普通代码控制。
- **故障恢复与幂等（Idempotency）**：处理重复请求、响应丢失和结果未知，避免重复业务效果。
- **Evaluation / LangSmith**：固定案例、确定性业务验证、模型评审与应用追踪。

## 系统架构

```text
User → Customer Agent → RAG / Handoff → Support Agent → Read-only Tools
                                                     ↓
                                           Diagnosis / Action Plan
                                                     ↓
                                         Confirmation / Approval
                                                     ↓
                                       Business Action → Verification
                                                     ↓
                                            Result / Engineer Ticket

Platform ← HTTP → Merchant + Worker ← HTTP → Warehouse
                         ↓
                PostgreSQL + pgvector
```

CLI、FastAPI 和 React 使用同一套会话与业务函数。Customer Agent 不读取后台订单或库存状态；Support Agent 的模型只选择读取工具和建议动作，不能授予权限或判定写入成功。详见[架构说明](docs/architecture.md)和[模拟器演示](docs/agent-simulator.md)。

## RAG

- 递归导入 Markdown、PDF、DOCX、XLSX。当前有 23 份正式来源，文档按正文分块，表格按完整记录分块。
- 使用 PostgreSQL + pgvector 保存资料、向量及检索记录，按公司、产品、版本和有效期过滤。
- 保留 Vector、Hybrid 和 Hybrid + Rerank 三种模式；示例配置默认使用 `hybrid`。
- 参考 WeKnora 的 Query Rewrite 与 Retrieval Diversity 思路，保留问题实体和条件，并限制单一来源占据全部检索结果。没有完整实现 WeKnora 或 MMR。
- 当前 Quick RAG 正常路径通常为简单问题 1 次 LLM 调用，复杂问题按需增加查询规划至 2 次。重试与 Fallback 可能增加实际调用。
- 引用通过片段 ID、公司、产品、版本、日期和连续原文检查。确定性验证能拒绝无效引用，但不能保证答案语义完全正确。

知识来源见[产品资料目录](docs/product-catalog.md)，历史取舍见[RAG 优化记录](evals/optimization/rag.md)。

## Agent Workflow

**Customer Agent** 根据问题决定查询资料、澄清或交接。它只使用产品知识，不把用户描述当作已经验证的后台事实。

**Support Agent** 接收同一会话中的结构化 Handoff，再根据已有证据选择工具。调查有时间、调用和错误预算，可以在证据充分时停止，也可以明确保留未知状态或转人工。

诊断的最后一次模型响应同时给出结论和候选动作。系统支持订单重试、发货补传、库存刷新和已有失败任务重试；重新授权由用户完成，没有模型可调用的自动 OAuth 执行器。Engineer Ticket 用于人工升级，不承担两个 Agent 之间的通信。

## 安全与可靠性

用户确认与管理员审批按动作范围区分。实际执行前重新核对权限、对象范围和当前事实，不能沿用已经失效的证据。

稳定请求编号、唯一约束、执行租约和回执对账共同防止重复业务效果。响应丢失时先查询原请求状态，无法证明结果时保留等待或未知，不能直接声称成功。最终结果由业务回读验证决定。

目标是 Effectively-once Business Effect，不承诺严格 Exactly-once Execution。库存刷新、订单恢复、发货补传和工单复查沿用相同的安全边界。

## Evaluation Results

下表是已经保存的历史阶段结果，不是对当前代码重新运行的最终验收。开发集与 Holdout 分开报告，失败和评分修正保留在[完整历史](evals/BENCHMARK_HISTORY.md)。

| 模块 | 代表性结果 | 范围与限制 |
|---|---|---|
| Workflow | Task Success **65% → 97.5%**；Tool Selection / Argument **97.5%** | 开发与回归案例 26/40 → 39/40；首次 Holdout 8/10 |
| RAG | **Recall@5 95%** | 历史开发阶段；同次运行自动 Task Success 19/30，离线复核 26/30（86.67%），仍有真实失败与回归 |
| Safety | 优化后 **20/20**，Holdout **5/5**；观察到不安全业务副作用 **0** | 确定性 API / SQL 场景验证，不等于所有自由文本攻击均安全 |
| Reliability | **76.9% → 100%**；Duplicate Business Effect **0**；False Success **0** | 开发案例 10/13 → 13/13；Holdout 原成绩仍为 4/5，标签勘误未改写成绩 |
| Performance | 代表性请求 **P95 112.6s → 23.6s** | 五请求 profiling；不是最终 Quality-certified Benchmark，最终质量门槛未通过 |

## 性能优化

LangSmith tracing 用于定位串行模型调用、冗余 Review 和重复上下文。历史优化包括移除独立 Completeness Review 与语义 Citation LLM、精简重复元数据、复用同一 Decision 中已有的证据，并保留执行前的最新事实检查。

RAG 正常路径由历史 3–4 次调用减少到通常 1–2 次，引用范围和原文锚点由确定性代码检查。降低 Token 数不一定降低账单：历史 Provider 价格上涨曾抵消部分收益。造成答案缺失或回归的实验已撤回，当前代码没有新的完整质量认证。详见[性能优化记录](evals/optimization/performance.md)。

## 项目结构

```text
backend/       API、CLI、Agent、RAG、安全执行与验证
frontend/      React 演示界面与浏览器测试
simulator/     业务服务、Worker、初始化与演示场景
infra/         SQL migrations
tests/        单元、契约与确定性回归测试
docs/          架构、演示、产品资料与历史建设记录
evals/         评测入口、数据、评分、Benchmark 与优化历史
```

`reports/` 和 `.local/` 保存本地运行证据、费用账本与凭证，均被 Git 忽略。历史建设计划位于 `docs/history/`。

## 本地运行

需要 Python 3.11+、Node.js 22+ 和 Docker Desktop。以下命令从仓库根目录执行。

```powershell
Copy-Item .env.example .env
# 编辑 .env：设置本地数据库密码，以及需要使用的模型和 Embedding 配置
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.lock
docker compose up -d --build
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
```

Compose 的一次性 `init` 服务执行 migrations 并初始化模拟数据，在 `.local/` 生成演示用户和服务凭证。Compose 项目名保持 `v2`，以沿用既有本地数据库卷；不要删除数据卷来完成仓库清理。重新执行初始化可能刷新用户凭证。

知识导入需要可用的 Embedding 配置并产生外部调用。准备好配置后单独执行：

```powershell
.\.venv\Scripts\python -m backend.app.cli docs import
```

前端在另一个终端运行：

```powershell
cd frontend
npm ci
npm run dev
```

默认访问 `http://127.0.0.1:5173`，API 文档位于 `http://127.0.0.1:8000/docs`。商家和工程师入口使用 `.local/test_tokens.json` 中对应的本地凭证。模型问答会产生外部调用；订单、发货与工单的确定性演示见[演示步骤](docs/demo.md)。

已有测试使用隔离 PostgreSQL 数据库与模拟模型，不调用真实 LLM：

```powershell
.\.venv\Scripts\python -m pytest
```

## Evaluation

- [评测设计、指标与运行方式](evals/README.md)
- [完整 Benchmark 历史](evals/BENCHMARK_HISTORY.md)
- 优化记录：[Workflow](evals/optimization/workflow.md)、[RAG](evals/optimization/rag.md)、[Safety](evals/optimization/safety.md)、[Reliability](evals/optimization/reliability.md)、[Performance](evals/optimization/performance.md)

只检查数据集路径和字段，不运行评测：

```powershell
.\.venv\Scripts\python -m evals.run --validate
```

真实 Evaluation、完整 Benchmark 和 Holdout 应单独明确安排，不能作为普通测试或清理验证运行。

## 已知限制

- Simulator 不等于 Production：没有真实商业平台、物流或自动 OAuth 集成。
- Evaluation Dataset 有限，历史开发集成绩不能代表生产分布。
- RAG 的答案完整性、业务阶段理解和原文锚点仍有边界问题；检索命中不等于回答正确。
- Performance 的最终 Quality Gate 未完成，代表性 profiling 不构成当前版本的质量或时延保证。
- LangSmith 项目中的远端追踪可能需要账户访问；GitHub 可直接阅读 Benchmark 与优化记录，本地原始报告不会随克隆下载。
