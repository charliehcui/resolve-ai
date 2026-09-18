# ResolveAI V2 演示路径

所有默认演示保持 `LANGSMITH_TRACING=false`。前两条业务路径不调用外部模型；模型问答只在以后明确授权时单独运行。

## 1. 启动

```powershell
cd E:\AI_Engineer\Langchain\resolve-ai\V2
Copy-Item .env.example .env
# 设置本地数据库密码，模型变量可保留安全占位值
docker compose up -d --build
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
$tokens = Get-Content .local\test_tokens.json | ConvertFrom-Json
```

Compose 会先完成 migration/bootstrap，再启动 Platform、Merchant、Warehouse、Worker 和 API。

## 2. 确定性订单与发货业务流

```powershell
$env:RESOLVEAI_TOKEN = $tokens.'admin-a'
.\.venv\Scripts\python -m simulator.lab.cli order create --shop shop-a --order O-DEMO-1 --sku SKU-1 --qty 2 --amount-minor 20000
.\.venv\Scripts\python -m simulator.lab.cli order dispatch --shop shop-a --order O-DEMO-1
.\.venv\Scripts\python -m simulator.lab.cli shipment create --shop shop-a --order O-DEMO-1 --carrier test-express --tracking DEMO-TRACK-1
.\.venv\Scripts\python -m simulator.lab.cli shipment show --shop shop-a --order O-DEMO-1
```

检查点：Platform、Merchant 和 Warehouse 保存独立事实；Warehouse 只有一次实际 Shipment；平台状态来自真实 HTTP/Worker 流程。

## 3. 库存只读流程

```powershell
.\.venv\Scripts\python -m simulator.lab.cli stock publish --shop shop-a --sku SKU-1 --warehouse-sku MERCHANT-SKU-1 --physical 80 --reserved 10
```

检查点：Warehouse 为 80/10，Merchant safety stock 为 5，Platform 发布 65；Support 不具备库存写工具。

## 4. Ticket 复查和导出

React 页面：

```powershell
cd frontend
npm ci
npm run dev
```

在 `http://127.0.0.1:5173` 中：

1. 使用 `admin-a` token 创建 Conversation。
2. 直接点击 `Request engineer` 创建一般 Ticket。
3. 使用 `engineer-a` token 加载分配队列并打开 Ticket。
4. 点击 `Recheck business result`。
5. 由于一般 Ticket 没有 shop/order/SKU，结果必须是 `NEEDS_INFO`，状态保持 `open`。

CLI 可复查已有订单、发货或库存 Ticket：

```powershell
.\.venv\Scripts\python -m backend.app.cli ticket recheck <ticket-id> --token $tokens.'engineer-a'
.\.venv\Scripts\python -m backend.app.cli ticket export <ticket-id> --token $tokens.'engineer-a'
```

业务事实满足对应 Verification 时返回 `RESOLVED` 并关闭；仍不满足时返回 `UNRESOLVED`；信息不足时返回 `NEEDS_INFO`。HTML 默认写入 `V2/reports/`，该目录被 Git 忽略。

## 5. 可选模型问答

以下步骤会调用真实 Groq/Google/Embedding，因此不属于当前本地验收。只有以后明确批准时才配置真实 Key 并运行：

```powershell
.\.venv\Scripts\python -m backend.app.cli docs import
.\.venv\Scripts\python -m backend.app.cli doctor
.\.venv\Scripts\python -m backend.app.cli chat "如何开启订单同步？"
```

不要为了演示补跑 176 次 Benchmark、Holdout 30×3 或批量 LangSmith Trace。

