# ResolveAI V2

ResolveAI V2 是一个用于演示 AI Application Engineering 的电商商家管理软件支持系统。当前完成 Phase 3 / Task 04：除带引用的 Customer RAG 外，测试操作者还能创建平台订单，让订单经过真实 HTTP 事件、商家接收记录和后台任务进入商家订单，或由真实业务状态阻止处理。

## 当前真实数据流

```text
终端问题
  → 本地 token 身份映射
  → Groq 查询规划 / 最多一次 Query Rewrite / Clarification 或 Handoff Intent
  → 公司、产品、版本、有效期过滤
  → Google 1024 维 Vector Search
  → 可选中文 BM25 + RRF + Qwen3 Reranking
  → Groq 生成可核查 Claims
  → 普通代码校验引用存在性、公司范围、版本与有效期
  → Groq 检查 Claim Support；删除无依据结论
  → 保存消息、检索记录、模型用量和 trace ID
  → 终端显示回答与来源
```

订单业务切片：

```text
Lab CLI 创建已付款订单
  → Platform HTTP API 校验账户公司并保存源订单
  → Platform 通过 HTTP 发送订单事件
  → Merchant 同一事务保存接收记录与 pending task
  → 独立 Worker 读取任务并检查店铺开关、渠道连接、付款和 SKU 映射
  → 条件满足：创建唯一商家订单并完成任务
  → 条件不满足：保存 blocked/failed 状态和真实错误码，不创建商家订单
  → Lab 分别查询平台、任务和商家订单事实
```

当前只有 Handoff Intent，没有真正切换角色。项目没有 Support Agent、调查 Tools、Action、Approval、Ticket、发货/仓库模拟器、完整 API 或 React UI。这些能力不能从当前 README 推断为已经完成。

## 环境要求

- Python 3.11+
- Docker Desktop
- 已准备且被 Git 忽略的 `V2/.env`
- Groq、Google 和 LangSmith 配置

模型名称和 Key 只从 `.env` 读取。不要把 `.env`、`.local/test_tokens.json` 或任何 Secret 提交到 Git。

## 安装与启动

```powershell
cd E:\AI_Engineer\Langchain\resolve-ai\V2
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.lock
docker compose up -d db
.\.venv\Scripts\python -m app.cli db init
.\.venv\Scripts\python -m lab.bootstrap
.\.venv\Scripts\python -m app.cli docs import
.\.venv\Scripts\python -m app.cli doctor
docker compose up -d --build merchant platform worker
```

`lab.bootstrap` 会在被忽略的 `.local/test_tokens.json` 生成本机测试 token，不会把 token 写入文档或提示词。

第一次运行 `hybrid_rerank` 会从 Hugging Face 下载 `RERANK_MODEL` 指定的 Qwen3 Reranker 权重；权重保存在用户缓存中，不进入仓库。

## 终端问答

PowerShell 示例：

```powershell
$tokens = Get-Content .local\test_tokens.json | ConvertFrom-Json
$env:RESOLVEAI_TOKEN = $tokens.'admin-a'
.\.venv\Scripts\python -m app.cli chat "如何开启订单同步？"
```

选择检索模式：

```powershell
.\.venv\Scripts\python -m app.cli chat "ORDER_SYNC_DISABLED 是什么意思？" --mode vector_only
.\.venv\Scripts\python -m app.cli chat "ORDER_SYNC_DISABLED 是什么意思？" --mode hybrid
.\.venv\Scripts\python -m app.cli chat "ORDER_SYNC_DISABLED 是什么意思？" --mode hybrid_rerank
```

当前默认是 `vector_only`。4 个 Phase 2 开发案例中三种模式均为 4/4 Top-5 命中；`vector_only` 平均延迟最低且预期资料排名最好。三种模式仍保留用于后续评估，不把小样本结论扩大为普遍结论。

继续同一会话：

```powershell
.\.venv\Scripts\python -m app.cli chat "这个开关会补回全部历史订单吗？" --conversation <conversation-id>
```

## 订单业务流

正常同步：

```powershell
.\.venv\Scripts\python -m lab.cli shop sync --shop shop-a --enabled true
.\.venv\Scripts\python -m lab.cli order create --shop shop-a --order O-1001 --sku SKU-1 --qty 2 --amount-minor 20000
```

产生真实受阻任务：

```powershell
.\.venv\Scripts\python -m lab.cli shop sync --shop shop-a --enabled false
.\.venv\Scripts\python -m lab.cli order create --shop shop-a --order O-1002 --sku SKU-1 --qty 1 --amount-minor 5000
```

第二条命令仍会留下平台订单、商家接收记录和 `ORDER_SYNC_DISABLED` 任务，但不会生成商家订单。重新发送完全相同的订单会复用稳定事件编号；内容变化则返回冲突。Phase 3 不提供自动恢复动作。

## 订单恢复

先由 Support case 创建一笔受限方案，再由同公司管理员批准：

```powershell
.\.venv\Scripts\python -m app.cli action propose <case-id>
.\.venv\Scripts\python -m app.cli action decide <action-id> --decision approve
.\.venv\Scripts\python -m app.cli action show <action-id>
```

如果方案需要同时开启店铺订单同步，必须在提案时显式增加 `--enable-order-sync`。批准有效期为十分钟。批准后程序重新检查公司、店铺、源订单版本和店铺版本，再通过 Merchant HTTP repair contract 创建幂等任务。Worker 完成后，独立 Verification 重新查询 Platform 与 Merchant，并核对事件、SKU、数量、金额、任务状态和唯一订单数。重复批准不会新增 receipt、decision 或业务订单。

## 测试

测试使用真实 PostgreSQL + pgvector；普通 unit、contract、graph 和 regression tests 使用确定性 mock/stub，并强制关闭 LangSmith tracing，不消耗 Groq/Google 调用：

```powershell
.\.venv\Scripts\python -m pytest
```

真实模型验收使用 `doctor`、`evals/dev.jsonl` 和三个 `--mode` 入口。模型用量不可获得时记录为 `unknown`，不记作零成本。

## 当前限制

- 当前资料集只有 14 个片段，检索质量对照只是小型开发验证，不代表大规模性能。
- 本地 Qwen3 Reranker 真实可运行，但 CPU 延迟明显高于 Vector/Hybrid；默认模式会随后续真实数据重新评估。
- Citation Validation 会降低无依据回答风险，但语义模型仍可能误判，不宣称保证答案正确。
- Customer Agent 不读取任何真实订单、店铺、平台、仓库、日志或库存后台状态。
- 订单模拟器只覆盖已付款单订单及必要拒绝状态；不连接真实平台，不做拆单或促销分摊。
- Phase 3–5 的业务安全、批准、执行和验证由普通代码决定；LLM 不负责最终写入许可或成功判定。
- Phase 3 的 Platform、Merchant 和 Worker Trace 已在 LangSmith `resolveai-v2` 项目中远端验证。
- Phase 4 的完整真实调查与远端 Trace 因外部 503/429 标记为 `External Acceptance Deferred`，集中到 Phase 9/最终验收；本地 Evidence 和运行错误仍会保存。
- Phase 5 已实现订单恢复，但完整 response-lost、worker crash、并发 lease 回收与两条恢复主线统一留到 Phase 6。
- 产品规则属于本项目测试产品，不代表真实电商平台。
- Docker 启动、模型地域和账户额度可能影响本机验收。
