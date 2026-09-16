# ResolveAI 3–5 Minute Demo Script

## Preparation

Run the full application:

```powershell
docker compose up --build
```

Open these demo URLs in separate browser profiles or start a new conversation before changing URL:

- Direct customer solution: `http://127.0.0.1:3000/?customer=customer_002`
- Automatic ticket and support resolution: `http://127.0.0.1:3000/?customer=customer_001`
- Approval path: `http://127.0.0.1:3000/?customer=customer_003`
- Engineer escalation: `http://127.0.0.1:3000/?customer=customer_004`

## 0:00–0:40 — Problem and boundary

“ResolveAI helps a customer describe a problem in normal language. It reads information the system already has, avoids asking the customer for technical records, and only gives an answer when the answer is supported.”

Show Customer View. Point out that customer text is Simplified Chinese and internal fields are not visible.

## 0:40–1:20 — Direct customer solution

Use `customer_002` and enter: `我的订单通知收不到了。`

Show the automatically collected account, version, feature setting, and recent activity. Show the customer document citation and the short suggested steps. Confirm recovery with: `开启订单通知后，我现在可以正常收到通知了。`

## 1:20–2:00 — Automatic ticket and support resolution

Use `customer_001` and enter: `订单完成了，但是提醒一直没有送到。`

Switch to Support View. Show the handoff, the exact tools used, saved evidence, internal document reference, and final customer-safe answer. Explain that the customer did not repeat information already collected by the system.

## 2:00–3:00 — Human approval and one-time execution

Use `customer_003` and enter: `我的报表一直下载不下来。`

Show the customer waiting message. Switch to Support View and show the proposed action, evidence, target, and expected result. Select Approve. Show that execution is saved once and the operation is read again before the customer receives a success message.

## 3:00–3:40 — Engineer escalation

Use `customer_004` and enter: `报表显示失败，我一直拿不到文件。`

Switch to Support View. Show the missing or conflicting evidence and the engineer package. Explain that the system does not guess when the available records cannot support a reliable answer.

## 3:40–4:30 — Evaluation and safety evidence

Show `evals/reports/resolveai_eval_v1.json`. Point out the 24 saved cases, graph paths, tools, citations, evidence, outcome, latency, model calls, token use, cost estimate, approval bypass count, and repeated-write result.

Finish with: “The models can understand and propose. Server code owns customer identity, evidence checks, approval, execution, and final verification.”

## Screenshot set

- `screenshots/customer-resolution.png`
- `screenshots/support-resolution.png`
- `screenshots/approval-waiting.png`
- `screenshots/engineer-escalation.png`
