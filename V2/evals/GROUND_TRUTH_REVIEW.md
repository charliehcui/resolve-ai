# Workflow Ground Truth Review — 50 例冻结

审查日期：2026-10-03（Australia/Sydney）。只依据当前真实业务代码与 Simulator 规则，没有读取 Agent Evaluation 结果，没有修改 Agent、Prompt 或 Workflow 业务逻辑，没有模型调用。

最终 50 例：Development 17 + Regression 23 = 40；全新 Holdout 10。保留原有 34 例，新增 16 例，没有删除或合并。原有 12 个历史 Holdout 全部降为 Regression，Case ID 的历史前缀不决定分组，`expected.split` 是唯一依据。

逐例完整标准保存在 `smoke.jsonl` 的 `workflow_ground_truth`，包括必须确认的事实、语义 Diagnosis、任一合法读取路径、Optional / Unnecessary Tools、参数、Handoff、Action、Task Success、Unsupported Claims、真实代码符号和 Fixture 断言。初始业务状态在 `initial_state.business_facts`。事实列表须全部满足，不能只命中任一关键词。

## 既有 34 例逐例审查

| Case ID | 审查结果与业务边界 |
|---|---|
| flow-order | 保留。暂时处理失败，允许订单恢复或失败任务重试；候选证据按合法动作路径确定。 |
| flow-shipment | 保留。真实仓库与商家发货匹配、平台缺失，可重发已有数据，不能再次出库。 |
| flow-stock | 保留。来源版本未发布且应为 65，可提出受控刷新。 |
| flow-worker | 保留。processing + WORKER_INTERRUPTED + 超过 60 秒，可 retry_failed_task。 |
| flow-auth | 保留。当前授权过期，需商家重新授权，不能生成凭据或直接重试。 |
| flow-outage | 保留。当前 503 不可用，等待后重查；不能承诺失败任务恢复后自动重试。 |
| flow-limit | 保留。429 限流不同于授权过期，没有当前恢复动作，不强制人工。 |
| flow-mapping | 保留并放宽读取路径。处理记录或 Worker 错误均可确认映射阻碍，需人工。 |
| flow-info | 保留。缺店铺和订单信息，只补问；隐藏 Fixture 编号不是用户信息。 |
| flow-human | 保留。直接要求工程师，创建真实工单，无需业务调查。 |
| flow-transfer | 保留并澄清。Customer → Support 交接不同于工程师工单，不作未读后台诊断。 |
| flow-dev-missing-sku | 保留。店铺已知，只补问 SKU，不额外索要订单编号。 |
| flow-dev-unpaid | 保留。未付款来源不可导入，不能承诺付款后阻塞任务自动执行。 |
| flow-dev-completed | 保留。实际身份、SKU、数量、金额匹配后结束；任务 completed 单独不够。 |
| flow-dev-undelivered | 保留。平台存在而接收为空，可单笔恢复；不能声称已查明隐藏网络丢失原因。 |
| flow-dev-stock-source | 保留。规则存在但来源缺失，不得推断为零，需人工。 |
| flow-dev-read-failure | 保留双合法结果。读取 503 保留未知，等待或实际转人工均可，不能利用隐藏已完成状态。 |
| flow-holdout-sync-off | Regression。当前订单同步关闭，用户禁止改设置，没有当前恢复方案。 |
| flow-holdout-not-shipped | Regression。仓库待出库，空平台发货不是转发失败证据。 |
| flow-holdout-stock-matched | Regression。数量与版本一致，结束，无需刷新。 |
| flow-holdout-stock-newer | Regression。平台版本更新，不能回退版本，需人工。 |
| flow-dev30-missing-order | 保留。发货工具需要 order_id，不制造独立 shipment_id 要求。 |
| flow-dev30-restored-auth | 保留。历史授权错误、当前已恢复，可 retry_order_sync；blocked 不可 retry_failed_task。 |
| flow-dev30-shipment-receipt | 保留，修正初始化竞争。平台已接受；任务可 unknown 或已核对 completed，均不重发。 |
| flow-dev30-stock-rule | 保留。规则停用，返回 STOCK_MAPPING_MISSING，不证明仓库库存缺失。 |
| flow-dev30-source-conflict | 保留。历史成功但当前来源数量冲突，不能覆盖，需人工。 |
| flow-holdout30-shipment-conflict | Regression。仓库与平台运单不同，不能擅自覆盖一方。 |
| flow-holdout30-stock-retry | Regression。历史发布失败、当前连接恢复，应为 75，可提出刷新。 |
| flow-holdout30-sync-read-error | Regression。当前设置读取失败，旧错误不能证明当前关闭；等待或人工均可。 |
| flow-holdout30-diagnose-only | Regression。只解释已确认事实，用户拒绝方案和人工，是独立决策边界。 |
| flow-holdout34-task-facts-only | Regression。用户拒绝人工及重试，说明中断事实，不创建方案或工单。 |
| flow-holdout34-source-absent | Regression。来源查无此编号，请核对编号，不猜同步故障或继续无关调查。 |
| flow-holdout34-stock-zero-floor | Regression。正实物但可售公式归零，版本一致，是合法零库存。 |
| flow-holdout34-platform-without-dispatch | Regression。平台有运单而仓库未出库；两方证据已足够，转发读取改为 Optional；条件满足后人工。 |

相似案例保留的是不同业务条件或不同用户要求，没有通过只更换参数增加案例。新增 6 Development 与 10 Holdout 的完整标准均在数据中；总览见 WORKFLOW_EVAL_SPEC.md。

## 修正依据

- `support_action_plans.create_action_plan`：retry_failed_task 的候选证据只要求 GetWorkerTask；retry_order_sync 要 GetOrder + GetOrderProcessRecords。因此 flow-order 允许任一完整路径，计划层仍重新查询全部条件。
- `merchant.internal_worker_task` 返回订单任务 error_code，SKU_MAPPING_MISSING 可由 Worker 或处理记录确认，不应唯一锁定处理工具。
- `warehouse.shipment_fact`、`platform.internal_get_shipment`、`build_shipment_action_plan`：零仓库发货而平台有运单已证明冲突，旧案例不必强制商家转发查询。
- `worker.reconcile_next_unknown_shipment` 会在平台事实匹配时将 unknown 改成 completed。修正旧 Fixture 的单一状态断言，业务答案不变。
- `stock.assess_stock_facts` 与 `merchant.internal_stock_records` 区分来源缺失、规则缺失、零库存和版本冲突，标签使用真实返回字段。
- 真正函数名是 `worker.process_next_task`，不是旧来源文字中的 process_next_order_task；其只处理 pending，`merchant.set_connection` 不重新入队。禁止无依据自动重试承诺的标准保留。
- 已存在的单次读取失败等待/人工双答案标签保留，新发货中间读取失败同样允许两种结果，状态须与实际工单一致。

## 完成标准与兼容字段

Required Tool Route 是集合，不规定顺序。动作还需引用 `action.candidate_required_tools` 并满足业务条件。Optional 只在补充用户要求或实际不确定性时合法；证据齐全后无理由辅助读取算 Unnecessary Tool。同范围同事实的无理由再次读取算 Repeated Tool；错误后的有界重试允许，计划/验证层系统读取不算 Agent 重复工具。没有固定总调用数门槛。

本 Dataset 评估单次调查决策：恢复动作成功是创建真实、可审核、待批准方案，不能擅自执行；重新授权是带当前证据的 user_action_required 指引。no_repair 要求本轮不写业务数据。人工需要真实工单，Support 交接需要真实交接。拒绝人工时如实说明未解决限制，不能伪造已解决。

`diagnosis_any` 和旧关键词/状态评分只是兼容字段。当前 `evals/metrics.py` 没有执行全部新增语义、路线和工具时机条件；以后评价器必须依据 `workflow_ground_truth` 判定任务完成，不能将旧关键词命中称为通过完整标准。本次按要求没有修改评分器或让 Agent 答题。

## 固定 Failure Categories

| Category | 判定 |
|---|---|
| Wrong Tool | 工具不存在、禁止或不能调查目标事实。 |
| Missing Tool | 未完成任何合法必需路径，或动作缺必要证据。 |
| Unnecessary Tool | 读取与任务无关，或证据齐全后无理由继续。 |
| Repeated Tool | 同范围同事实重复，没有重试理由、状态变化或新增证据。 |
| Wrong Argument | 编号、范围、参数字段或值错误。 |
| Wrong Diagnosis | 诊断与已确认事实或业务规则冲突。 |
| Wrong Handoff | 人工决定违反事实或用户限制，或混淆 Support 与人工。 |
| Premature Stop | 用户要求的事实、动作或交接未完成就结束。 |
| Incomplete Action | 应有合法方案/工单却只有文字承诺或缺关键记录。 |
| Unsupported Claim | 根因、结果、当前状态或自动处理承诺没有证据。 |
| Invalid Action | 业务条件不满足、超范围或违背用户明确限制。 |
| Provider Error | LLM/provider 请求失败；Simulator 读取失败是案例输入。 |
| Evaluation Error | Fixture、加载、评分、观察或冻结检查错误，不能当作 Agent 能力失败。 |

一个结果可以有多个失败类别，不增加更细分类。

## 冻结和验证

Canonical Workflow Dataset SHA256：`01692ea2bc786b5f21da8c75d78e18eef9d3409fa79462c09ee9c533c6e424b1`。
Fixture 定义 SHA256：`1ec1f234907299417035d520d92b32bb0c2876e6de10ead65a910202e5b1b9c8`。

哈希算法：只取 workflow 条目，以 EvalCase.model_dump 规范化并按 case_id 排序；连同 version=workflow-50-frozen-v1 和 failure_categories 封装为对象，再以 UTF-8、ensure_ascii=False、sort_keys=True、separators=(",", ":") 序列化求 SHA256。包含问题、分组、初始状态、全部 Ground Truth、断言和固定失败类别；不包含运行时 UUID/时间。Fixture Hash 为 evals/scenarios.py 文件字节 SHA256。下表另保存本次业务规则代码哈希，方便以后定位规则变化。

`python -m evals.dataset --validate` 检查完整性、唯一编号、分组、真实来源符号和冻结哈希。`--check-fixtures` 在新建 resolveai_eval_* 数据库初始化并读取同样的 Simulator 接口，库存使用真实 assess_stock_facts 规则；禁止外部 HTTP，不加载 Agent/LLM 模块，不创建对话、模型 Diagnosis、方案或工单。每例建好后停止 Worker，再检查事实，避免下一例重置的锁竞争；结束清理本次自建数据库。这是 Dataset 检查，不是 Benchmark。

验证结果：50/50 Fixture 业务断言通过；唯一编号、完整字段、分组和真实来源符号检查通过；模型调用 0，Agent 回合 0。全部新标签在任何优化前按业务代码制定，10 个 Holdout 从未运行模型 Evaluation。

Optimization Agent 只可运行并优化 Development/Regression，不可改 Dataset、Fixture、分组、Ground Truth、冻结常量和 Hash。发现真实标签疑点时单独记录代码依据，等待人工确认，不得自行改答案或换 Holdout。默认 Quick 只选 Development；现有 Final 会包含 Holdout，未经单独授权不能运行。

## 业务规则代码快照

| Source | SHA256 |
|---|---|
| `backend/app/handoff.py` | `c66904ec46e3e801ddfb15f5729422b7e2f8a640ae34002a8d41e4b1066b6cf9` |
| `backend/app/stock.py` | `cecdd33645c107bf712d473b65ff57083b896b4ce91194793ccc33bb3e248522` |
| `backend/app/support_action_plans.py` | `c42257c8b42d699b53ce6e5f255a0c953521b260803f0de4ff0d7ef791e5e86e` |
| `backend/app/support_action_registry.py` | `806612749b07c9fae484208d65a852db46548cd5a62d61939f5182781f96f386` |
| `backend/app/support_action_verification.py` | `e76cda9dd244a11939740ab8cd974e0d7383d4cd248b05c4067d774311794572` |
| `backend/app/support_tools.py` | `751f541bb10de339272df5b8f609b0c1f9cd274f03296176d0cf8ac275ce13a4` |
| `backend/app/tickets.py` | `c6331f9eb818cb546d58a3b96ed75931631789894163a69568db932407187514` |
| `backend/app/user_intent.py` | `dca30f62202a5dae64946693a5758890346c5efa277762aed7f1a3cb6cd5000e` |
| `simulator/lab/scenarios.py` | `06c71a7f0e03873e4596572269dc4be8e6e84f7aa1ffc8eb22401189b8a763de` |
| `simulator/services/merchant.py` | `47f039e25a078ce4eac65702579690f60953de5e2747ccc77823614235705d37` |
| `simulator/services/platform.py` | `caed192fe03dd48aeafd0a99801a35d1f609846953649b8574512d515a57c57a` |
| `simulator/services/warehouse.py` | `64396e20674934e101786339103f15dd9c94a0c6ffa47b8ca814bb17e1ed94c8` |
| `simulator/services/worker.py` | `6ca47a742372934f8dd9f59c0e9082b416ba18b561ac83398cbcadbc5fee1cdf` |

## 历史 RAG 标签快照（本次未审查或修改）

以下保留原文件中的 RAG 记录，不属于本次 Workflow 冻结。

## rag-sync

**question**

产品 2.0 怎么开启订单同步？重新开启会自动补回全部历史订单吗？

**expected_answer_or_required_facts**

```json
{
  "expected_result": {
    "result": "grounded_answer"
  },
  "required_facts": [
    "管理员在店铺设置的订单同步菜单开启新订单同步",
    "重新开启不会自动补回全部历史订单，指定历史订单需要单独检查恢复"
  ]
}
```

**expected_documents**

```json
[
  "docs/product/01-order-sync-switch.md"
]
```

**expected_tools**

```json
{
  "acceptable_tools": [],
  "required_any": []
}
```

**expected_tool_arguments**

```json
{}
```

**expected_diagnosis**

```json
{
  "statuses": [],
  "diagnosis_any": [],
  "expected_action": null
}
```

**expected_handoff**

```json
false
```

**current_label_sources**

```json
{
  "dataset": "evals/smoke.jsonl",
  "definition": "evals/dataset.py:RAG_CASES",
  "declared_claim_source": "human-authored labels from original documents",
  "source_paths": [
    "docs/product/01-order-sync-switch.md"
  ],
  "declared_business_truth_source": null,
  "fixture": "documents"
}
```

## rag-paid

**question**

产品 2.0 接收哪些付款状态的订单？未付款或已取消的订单会生成管理软件订单吗？

**expected_answer_or_required_facts**

```json
{
  "expected_result": {
    "result": "grounded_answer"
  },
  "required_facts": [
    "只接收已付款的单订单",
    "未付款或已取消订单不会生成管理软件订单"
  ]
}
```

**expected_documents**

```json
[
  "docs/product/02-order-eligibility.md"
]
```

**expected_tools**

```json
{
  "acceptable_tools": [],
  "required_any": []
}
```

**expected_tool_arguments**

```json
{}
```

**expected_diagnosis**

```json
{
  "statuses": [],
  "diagnosis_any": [],
  "expected_action": null
}
```

**expected_handoff**

```json
false
```

**current_label_sources**

```json
{
  "dataset": "evals/smoke.jsonl",
  "definition": "evals/dataset.py:RAG_CASES",
  "declared_claim_source": "human-authored labels from original documents",
  "source_paths": [
    "docs/product/02-order-eligibility.md"
  ],
  "declared_business_truth_source": null,
  "fixture": "documents"
}
```

## rag-mapping

**question**

产品 2.0 的 SKU_MAPPING_MISSING 是什么意思？应该核对什么？

**expected_answer_or_required_facts**

```json
{
  "expected_result": {
    "result": "grounded_answer"
  },
  "required_facts": [
    "平台商品编号缺少到管理软件商品编号的映射",
    "核对店铺、平台 SKU 和当前商品映射"
  ]
}
```

**expected_documents**

```json
[
  "docs/product/03-sku-mapping.md",
  "docs/product/08-error-codes.md"
]
```

**expected_tools**

```json
{
  "acceptable_tools": [],
  "required_any": []
}
```

**expected_tool_arguments**

```json
{}
```

**expected_diagnosis**

```json
{
  "statuses": [],
  "diagnosis_any": [],
  "expected_action": null
}
```

**expected_handoff**

```json
false
```

**current_label_sources**

```json
{
  "dataset": "evals/smoke.jsonl",
  "definition": "evals/dataset.py:RAG_CASES",
  "declared_claim_source": "human-authored labels from original documents",
  "source_paths": [
    "docs/product/03-sku-mapping.md",
    "docs/product/08-error-codes.md"
  ],
  "declared_business_truth_source": null,
  "fixture": "documents"
}
```

## rag-completion

**question**

产品 2.0 怎么确认订单同步真正完成？任务提示成功就够了吗？

**expected_answer_or_required_facts**

```json
{
  "expected_result": {
    "result": "grounded_answer"
  },
  "required_facts": [
    "管理软件订单真实存在，商品、数量、金额一致",
    "页面或任务成功文字不能代替实际结果"
  ]
}
```

**expected_documents**

```json
[
  "docs/product/04-order-status.md"
]
```

**expected_tools**

```json
{
  "acceptable_tools": [],
  "required_any": []
}
```

**expected_tool_arguments**

```json
{}
```

**expected_diagnosis**

```json
{
  "statuses": [],
  "diagnosis_any": [],
  "expected_action": null
}
```

**expected_handoff**

```json
false
```

**current_label_sources**

```json
{
  "dataset": "evals/smoke.jsonl",
  "definition": "evals/dataset.py:RAG_CASES",
  "declared_claim_source": "human-authored labels from original documents",
  "source_paths": [
    "docs/product/04-order-status.md"
  ],
  "declared_business_truth_source": null,
  "fixture": "documents"
}
```

## rag-history

**question**

产品 2.0 支持一键导入全部历史订单吗？指定缺失订单应该怎么处理？

**expected_answer_or_required_facts**

```json
{
  "expected_result": {
    "result": "grounded_answer"
  },
  "required_facts": [
    "不支持一键导入全部历史订单",
    "符合资格的缺失订单需要后台证据、单笔恢复方案、批准和结果验证"
  ]
}
```

**expected_documents**

```json
[
  "docs/product/05-history-recovery.md"
]
```

**expected_tools**

```json
{
  "acceptable_tools": [],
  "required_any": []
}
```

**expected_tool_arguments**

```json
{}
```

**expected_diagnosis**

```json
{
  "statuses": [],
  "diagnosis_any": [],
  "expected_action": null
}
```

**expected_handoff**

```json
false
```

**current_label_sources**

```json
{
  "dataset": "evals/smoke.jsonl",
  "definition": "evals/dataset.py:RAG_CASES",
  "declared_claim_source": "human-authored labels from original documents",
  "source_paths": [
    "docs/product/05-history-recovery.md"
  ],
  "declared_business_truth_source": null,
  "fixture": "documents"
}
```

## rag-shipment

**question**

产品 2.0 仓库显示已发货能证明平台已更新吗？结果未知时应该如何恢复？

**expected_answer_or_required_facts**

```json
{
  "expected_result": {
    "result": "grounded_answer"
  },
  "required_facts": [
    "仓库出库、管理软件接收、管理软件发送、平台状态是独立事实",
    "结果未知时先查询平台回执和当前状态，不能再次命令仓库出库"
  ]
}
```

**expected_documents**

```json
[
  "docs/product/06-shipment-facts.md"
]
```

**expected_tools**

```json
{
  "acceptable_tools": [],
  "required_any": []
}
```

**expected_tool_arguments**

```json
{}
```

**expected_diagnosis**

```json
{
  "statuses": [],
  "diagnosis_any": [],
  "expected_action": null
}
```

**expected_handoff**

```json
false
```

**current_label_sources**

```json
{
  "dataset": "evals/smoke.jsonl",
  "definition": "evals/dataset.py:RAG_CASES",
  "declared_claim_source": "human-authored labels from original documents",
  "source_paths": [
    "docs/product/06-shipment-facts.md"
  ],
  "declared_business_truth_source": null,
  "fixture": "documents"
}
```

## rag-auth

**question**

产品 2.0 渠道授权过期后，Support 能自动生成新授权吗？其他店铺也会失效吗？

**expected_answer_or_required_facts**

```json
{
  "expected_result": {
    "result": "grounded_answer"
  },
  "required_facts": [
    "商家在平台重新授权，支持系统不能自行生成或修改授权",
    "一个店铺授权失效不能推断其他店铺也失效"
  ]
}
```

**expected_documents**

```json
[
  "docs/product/07-channel-authorization.md"
]
```

**expected_tools**

```json
{
  "acceptable_tools": [],
  "required_any": []
}
```

**expected_tool_arguments**

```json
{}
```

**expected_diagnosis**

```json
{
  "statuses": [],
  "diagnosis_any": [],
  "expected_action": null
}
```

**expected_handoff**

```json
false
```

**current_label_sources**

```json
{
  "dataset": "evals/smoke.jsonl",
  "definition": "evals/dataset.py:RAG_CASES",
  "declared_claim_source": "human-authored labels from original documents",
  "source_paths": [
    "docs/product/07-channel-authorization.md"
  ],
  "declared_business_truth_source": null,
  "fixture": "documents"
}
```

## rag-codes

**question**

产品 2.0 ORDER_SYNC_DISABLED 和 ORDER_NOT_PAID 分别表示什么？错误码能证明当前状态吗？

**expected_answer_or_required_facts**

```json
{
  "expected_result": {
    "result": "grounded_answer"
  },
  "required_facts": [
    "ORDER_SYNC_DISABLED 表示同步开关关闭，ORDER_NOT_PAID 表示未满足已付款资格",
    "错误码是记录的处理结果，不能证明当前状态仍然相同"
  ]
}
```

**expected_documents**

```json
[
  "docs/product/08-error-codes.md",
  "docs/product/02-order-eligibility.md"
]
```

**expected_tools**

```json
{
  "acceptable_tools": [],
  "required_any": []
}
```

**expected_tool_arguments**

```json
{}
```

**expected_diagnosis**

```json
{
  "statuses": [],
  "diagnosis_any": [],
  "expected_action": null
}
```

**expected_handoff**

```json
false
```

**current_label_sources**

```json
{
  "dataset": "evals/smoke.jsonl",
  "definition": "evals/dataset.py:RAG_CASES",
  "declared_claim_source": "human-authored labels from original documents",
  "source_paths": [
    "docs/product/08-error-codes.md",
    "docs/product/02-order-eligibility.md"
  ],
  "declared_business_truth_source": null,
  "fixture": "documents"
}
```

## rag-stock

**question**

产品 2.0 的上架数量公式是什么？实物 80、占用 10、安全保留 5 应上架多少？

**expected_answer_or_required_facts**

```json
{
  "expected_result": {
    "result": "grounded_answer"
  },
  "required_facts": [
    "上架数为 max(实物减占用减安全保留, 0)",
    "示例应上架 65"
  ]
}
```

**expected_documents**

```json
[
  "docs/product/10-stock-rule.md",
  "docs/product/08-stock-facts.md"
]
```

**expected_tools**

```json
{
  "acceptable_tools": [],
  "required_any": []
}
```

**expected_tool_arguments**

```json
{}
```

**expected_diagnosis**

```json
{
  "statuses": [],
  "diagnosis_any": [],
  "expected_action": null
}
```

**expected_handoff**

```json
false
```

**current_label_sources**

```json
{
  "dataset": "evals/smoke.jsonl",
  "definition": "evals/dataset.py:RAG_CASES",
  "declared_claim_source": "human-authored labels from original documents",
  "source_paths": [
    "docs/product/10-stock-rule.md",
    "docs/product/08-stock-facts.md"
  ],
  "declared_business_truth_source": null,
  "fixture": "documents"
}
```

## rag-scope

**question**

产品 2.0 支持自动退款、多仓分配或真实电商平台连接吗？

**expected_answer_or_required_facts**

```json
{
  "expected_result": {
    "result": "grounded_answer"
  },
  "required_facts": [
    "本期不支持自动退款、多仓分配或真实电商平台连接"
  ]
}
```

**expected_documents**

```json
[
  "docs/product/12-unsupported-features.md"
]
```

**expected_tools**

```json
{
  "acceptable_tools": [],
  "required_any": []
}
```

**expected_tool_arguments**

```json
{}
```

**expected_diagnosis**

```json
{
  "statuses": [],
  "diagnosis_any": [],
  "expected_action": null
}
```

**expected_handoff**

```json
false
```

**current_label_sources**

```json
{
  "dataset": "evals/smoke.jsonl",
  "definition": "evals/dataset.py:RAG_CASES",
  "declared_claim_source": "human-authored labels from original documents",
  "source_paths": [
    "docs/product/12-unsupported-features.md"
  ],
  "declared_business_truth_source": null,
  "fixture": "documents"
}
```

