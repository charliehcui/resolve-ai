# ResolveAI Phase 1.6 Ground Truth Review

10 RAG + 11 Workflow cases; labels copied without edits. Owner review is pending.
Dataset SHA256: 5b10e89bfcd56b897a293c512c7d9bea6b404914fcf8c45da2c327285e9416e8

Workflow diagnosis_any uses any-match semantics, rather than requiring all phrases. $shop_id / $order_id resolve to simulator fixture identifiers. Empty document lists mean no retrieval target is currently labeled.

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

## flow-order

用户确认修订（2026-10-03）：本场景允许两种重试动作。两者都必须满足已付款、同步开启、连接正常、有效映射、无商家订单及正确公司/店铺/订单范围，并引用真实证据；retry_order_sync 须引用订单和处理记录，retry_failed_task 还须引用同一订单的失败且 retryable=true 的任务。来源：support_action_plans.create_action_plan / build_order_action_plan 与 simulator 事实。只适用于本 Case。

**question**

订单处理失败，请调查

**expected_answer_or_required_facts**

```json
{
  "expected_result": {
    "statuses": [
      "awaiting_confirmation"
    ],
    "diagnosis_any": [
      "ORDER_SYNC_RETRYABLE",
      "失败",
      "重试",
      "retry",
      "fail"
    ],
    "truth_source": "simulator/lab/scenarios.py and simulator database facts"
  },
  "required_facts": [
    "ORDER_SYNC_RETRYABLE",
    "失败",
    "重试",
    "retry",
    "fail"
  ]
}
```

**expected_documents**

```json
[]
```

**expected_tools**

```json
{
  "acceptable_tools": [
    "GetOrder",
    "GetOrderProcessRecords",
    "GetShopSyncStatus",
    "GetShopConnectionStatus",
    "GetWorkerTask"
  ],
  "required_any": [
    [
      "GetOrder"
    ],
    [
      "GetOrderProcessRecords"
    ]
  ]
}
```

**expected_tool_arguments**

```json
{
  "GetOrder": {
    "shop_id": "$shop_id",
    "order_id": "$order_id"
  },
  "GetOrderProcessRecords": {
    "shop_id": "$shop_id",
    "order_id": "$order_id"
  },
  "GetShopSyncStatus": {
    "shop_id": "$shop_id"
  },
  "GetShopConnectionStatus": {
    "shop_id": "$shop_id"
  },
  "GetWorkerTask": {
    "shop_id": "$shop_id",
    "order_id": "$order_id"
  }
}
```

**expected_diagnosis**

```json
{
  "statuses": [
    "awaiting_confirmation"
  ],
  "diagnosis_any": [
    "ORDER_SYNC_RETRYABLE",
    "失败",
    "重试",
    "retry",
    "fail"
  ],
  "expected_action": "retry_order_sync",
  "acceptable_actions": ["retry_order_sync", "retry_failed_task"]
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
  "definition": "evals/dataset.py:WORKFLOW_CASES",
  "declared_claim_source": "human-authored scenario labels and initial database snapshot",
  "source_paths": [],
  "declared_business_truth_source": "simulator/lab/scenarios.py and simulator database facts",
  "fixture": "order_sync_failure"
}
```

## flow-shipment

**question**

发货同步失败，请调查

**expected_answer_or_required_facts**

```json
{
  "expected_result": {
    "statuses": [
      "awaiting_confirmation"
    ],
    "diagnosis_any": [
      "SHIPMENT_PUSH_RETRYABLE",
      "发货",
      "shipment"
    ],
    "truth_source": "simulator/lab/scenarios.py and simulator database facts"
  },
  "required_facts": [
    "SHIPMENT_PUSH_RETRYABLE",
    "发货",
    "shipment"
  ]
}
```

**expected_documents**

```json
[]
```

**expected_tools**

```json
{
  "acceptable_tools": [
    "GetWarehouseShipment",
    "GetShipmentProcessRecords",
    "GetPlatformShipment",
    "GetShopConnectionStatus",
    "GetShopSyncStatus",
    "GetOrder"
  ],
  "required_any": [
    [
      "GetWarehouseShipment"
    ],
    [
      "GetShipmentProcessRecords"
    ],
    [
      "GetPlatformShipment"
    ]
  ]
}
```

**expected_tool_arguments**

```json
{
  "GetWarehouseShipment": {
    "shop_id": "$shop_id",
    "order_id": "$order_id"
  },
  "GetShipmentProcessRecords": {
    "shop_id": "$shop_id",
    "order_id": "$order_id"
  },
  "GetPlatformShipment": {
    "shop_id": "$shop_id",
    "order_id": "$order_id"
  },
  "GetShopConnectionStatus": {
    "shop_id": "$shop_id"
  },
  "GetShopSyncStatus": {
    "shop_id": "$shop_id"
  },
  "GetOrder": {
    "shop_id": "$shop_id",
    "order_id": "$order_id"
  }
}
```

**expected_diagnosis**

```json
{
  "statuses": [
    "awaiting_confirmation"
  ],
  "diagnosis_any": [
    "SHIPMENT_PUSH_RETRYABLE",
    "发货",
    "shipment"
  ],
  "expected_action": "resend_shipment"
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
  "definition": "evals/dataset.py:WORKFLOW_CASES",
  "declared_claim_source": "human-authored scenario labels and initial database snapshot",
  "source_paths": [],
  "declared_business_truth_source": "simulator/lab/scenarios.py and simulator database facts",
  "fixture": "shipment_sync_failure"
}
```

## flow-stock

**question**

SKU-1 库存不一致，请调查

**expected_answer_or_required_facts**

```json
{
  "expected_result": {
    "statuses": [
      "awaiting_confirmation"
    ],
    "diagnosis_any": [
      "65",
      "库存",
      "stock"
    ],
    "truth_source": "simulator/lab/scenarios.py and simulator database facts"
  },
  "required_facts": [
    "65",
    "库存",
    "stock"
  ]
}
```

**expected_documents**

```json
[]
```

**expected_tools**

```json
{
  "acceptable_tools": [
    "GetStockStatus",
    "GetShopConnectionStatus",
    "GetShopSyncStatus"
  ],
  "required_any": [
    [
      "GetStockStatus"
    ]
  ]
}
```

**expected_tool_arguments**

```json
{
  "GetStockStatus": {
    "shop_id": "$shop_id",
    "sku": "SKU-1"
  },
  "GetShopConnectionStatus": {
    "shop_id": "$shop_id"
  },
  "GetShopSyncStatus": {
    "shop_id": "$shop_id"
  }
}
```

**expected_diagnosis**

```json
{
  "statuses": [
    "awaiting_confirmation"
  ],
  "diagnosis_any": [
    "65",
    "库存",
    "stock"
  ],
  "expected_action": "refresh_inventory"
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
  "definition": "evals/dataset.py:WORKFLOW_CASES",
  "declared_claim_source": "human-authored scenario labels and initial database snapshot",
  "source_paths": [],
  "declared_business_truth_source": "simulator/lab/scenarios.py and simulator database facts",
  "fixture": "inventory_mismatch"
}
```

## flow-worker

**question**

订单任务卡住，请调查

**expected_answer_or_required_facts**

```json
{
  "expected_result": {
    "statuses": [
      "awaiting_confirmation"
    ],
    "diagnosis_any": [
      "WORKER_INTERRUPTED",
      "中断",
      "processing",
      "卡",
      "stuck"
    ],
    "truth_source": "simulator/lab/scenarios.py and simulator database facts"
  },
  "required_facts": [
    "WORKER_INTERRUPTED",
    "中断",
    "processing",
    "卡",
    "stuck"
  ]
}
```

**expected_documents**

```json
[]
```

**expected_tools**

```json
{
  "acceptable_tools": [
    "GetWorkerTask",
    "GetOrder",
    "GetOrderProcessRecords",
    "GetShopConnectionStatus",
    "GetShopSyncStatus"
  ],
  "required_any": [
    [
      "GetWorkerTask"
    ],
    [
      "GetOrderProcessRecords"
    ]
  ]
}
```

**expected_tool_arguments**

```json
{
  "GetWorkerTask": {
    "shop_id": "$shop_id",
    "order_id": "$order_id"
  },
  "GetOrder": {
    "shop_id": "$shop_id",
    "order_id": "$order_id"
  },
  "GetOrderProcessRecords": {
    "shop_id": "$shop_id",
    "order_id": "$order_id"
  },
  "GetShopConnectionStatus": {
    "shop_id": "$shop_id"
  },
  "GetShopSyncStatus": {
    "shop_id": "$shop_id"
  }
}
```

**expected_diagnosis**

```json
{
  "statuses": [
    "awaiting_confirmation"
  ],
  "diagnosis_any": [
    "WORKER_INTERRUPTED",
    "中断",
    "processing",
    "卡",
    "stuck"
  ],
  "expected_action": "retry_failed_task"
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
  "definition": "evals/dataset.py:WORKFLOW_CASES",
  "declared_claim_source": "human-authored scenario labels and initial database snapshot",
  "source_paths": [],
  "declared_business_truth_source": "simulator/lab/scenarios.py and simulator database facts",
  "fixture": "worker_task_stuck"
}
```

## flow-auth

**question**

订单授权失效，请调查

**expected_answer_or_required_facts**

```json
{
  "expected_result": {
    "statuses": [
      "user_action_required"
    ],
    "diagnosis_any": [
      "授权",
      "auth",
      "401"
    ],
    "truth_source": "simulator/lab/scenarios.py and simulator database facts"
  },
  "required_facts": [
    "授权",
    "auth",
    "401"
  ]
}
```

**expected_documents**

```json
[]
```

**expected_tools**

```json
{
  "acceptable_tools": [
    "GetOrder",
    "GetOrderProcessRecords",
    "GetShopConnectionStatus",
    "GetShopSyncStatus",
    "GetWorkerTask"
  ],
  "required_any": [
    [
      "GetShopConnectionStatus"
    ]
  ]
}
```

**expected_tool_arguments**

```json
{
  "GetOrder": {
    "shop_id": "$shop_id",
    "order_id": "$order_id"
  },
  "GetOrderProcessRecords": {
    "shop_id": "$shop_id",
    "order_id": "$order_id"
  },
  "GetShopConnectionStatus": {
    "shop_id": "$shop_id"
  },
  "GetShopSyncStatus": {
    "shop_id": "$shop_id"
  },
  "GetWorkerTask": {
    "shop_id": "$shop_id",
    "order_id": "$order_id"
  }
}
```

**expected_diagnosis**

```json
{
  "statuses": [
    "user_action_required"
  ],
  "diagnosis_any": [
    "授权",
    "auth",
    "401"
  ],
  "expected_action": "request_reauthorization"
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
  "definition": "evals/dataset.py:WORKFLOW_CASES",
  "declared_claim_source": "human-authored scenario labels and initial database snapshot",
  "source_paths": [],
  "declared_business_truth_source": "simulator/lab/scenarios.py and simulator database facts",
  "fixture": "shop_authorization_expired"
}
```

## flow-outage

用户确认修订（2026-10-03）：最终回答不得承诺“恢复后自动重试”。来源：worker.process_next_order_task 只处理 pending 任务；merchant.set_connection 不会把失败任务重新入队。允许说明无法确认自动重试，或建议恢复后重新检查。此规则仅检查这项关键业务承诺，不代表完整语义评分。

**question**

第三方不可用，请调查订单

**expected_answer_or_required_facts**

```json
{
  "expected_result": {
    "statuses": [
      "retry_later"
    ],
    "diagnosis_any": [
      "503",
      "不可用",
      "unavailable"
    ],
    "truth_source": "simulator/lab/scenarios.py and simulator database facts"
  },
  "required_facts": [
    "503",
    "不可用",
    "unavailable"
  ]
}
```

**expected_documents**

```json
[]
```

**expected_tools**

```json
{
  "acceptable_tools": [
    "GetOrder",
    "GetOrderProcessRecords",
    "GetShopConnectionStatus",
    "GetShopSyncStatus",
    "GetWorkerTask"
  ],
  "required_any": [
    [
      "GetShopConnectionStatus",
      "GetOrderProcessRecords"
    ]
  ]
}
```

**expected_tool_arguments**

```json
{
  "GetOrder": {
    "shop_id": "$shop_id",
    "order_id": "$order_id"
  },
  "GetOrderProcessRecords": {
    "shop_id": "$shop_id",
    "order_id": "$order_id"
  },
  "GetShopConnectionStatus": {
    "shop_id": "$shop_id"
  },
  "GetShopSyncStatus": {
    "shop_id": "$shop_id"
  },
  "GetWorkerTask": {
    "shop_id": "$shop_id",
    "order_id": "$order_id"
  }
}
```

**expected_diagnosis**

```json
{
  "statuses": [
    "retry_later"
  ],
  "diagnosis_any": [
    "503",
    "不可用",
    "unavailable"
  ],
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
  "definition": "evals/dataset.py:WORKFLOW_CASES",
  "declared_claim_source": "human-authored scenario labels and initial database snapshot",
  "source_paths": [],
  "declared_business_truth_source": "simulator/lab/scenarios.py and simulator database facts",
  "fixture": "third_party_outage"
}
```

## flow-limit

**question**

订单渠道限流，请调查

**expected_answer_or_required_facts**

```json
{
  "expected_result": {
    "statuses": [
      "retry_later"
    ],
    "diagnosis_any": [
      "429",
      "限流",
      "rate"
    ],
    "truth_source": "simulator/lab/scenarios.py and simulator database facts"
  },
  "required_facts": [
    "429",
    "限流",
    "rate"
  ]
}
```

**expected_documents**

```json
[]
```

**expected_tools**

```json
{
  "acceptable_tools": [
    "GetOrder",
    "GetOrderProcessRecords",
    "GetShopConnectionStatus",
    "GetShopSyncStatus",
    "GetWorkerTask"
  ],
  "required_any": [
    [
      "GetShopConnectionStatus",
      "GetOrderProcessRecords"
    ]
  ]
}
```

**expected_tool_arguments**

```json
{
  "GetOrder": {
    "shop_id": "$shop_id",
    "order_id": "$order_id"
  },
  "GetOrderProcessRecords": {
    "shop_id": "$shop_id",
    "order_id": "$order_id"
  },
  "GetShopConnectionStatus": {
    "shop_id": "$shop_id"
  },
  "GetShopSyncStatus": {
    "shop_id": "$shop_id"
  },
  "GetWorkerTask": {
    "shop_id": "$shop_id",
    "order_id": "$order_id"
  }
}
```

**expected_diagnosis**

```json
{
  "statuses": [
    "retry_later"
  ],
  "diagnosis_any": [
    "429",
    "限流",
    "rate"
  ],
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
  "definition": "evals/dataset.py:WORKFLOW_CASES",
  "declared_claim_source": "human-authored scenario labels and initial database snapshot",
  "source_paths": [],
  "declared_business_truth_source": "simulator/lab/scenarios.py and simulator database facts",
  "fixture": "rate_limit"
}
```

## flow-mapping

**question**

订单商品映射缺失，请调查

**expected_answer_or_required_facts**

```json
{
  "expected_result": {
    "statuses": [
      "pending_human"
    ],
    "diagnosis_any": [
      "映射",
      "SKU_MAPPING_MISSING",
      "mapping"
    ],
    "truth_source": "simulator/lab/scenarios.py and simulator database facts"
  },
  "required_facts": [
    "映射",
    "SKU_MAPPING_MISSING",
    "mapping"
  ]
}
```

**expected_documents**

```json
[]
```

**expected_tools**

```json
{
  "acceptable_tools": [
    "GetOrder",
    "GetOrderProcessRecords",
    "GetShopConnectionStatus",
    "GetShopSyncStatus",
    "GetWorkerTask"
  ],
  "required_any": [
    [
      "GetOrderProcessRecords"
    ]
  ]
}
```

**expected_tool_arguments**

```json
{
  "GetOrder": {
    "shop_id": "$shop_id",
    "order_id": "$order_id"
  },
  "GetOrderProcessRecords": {
    "shop_id": "$shop_id",
    "order_id": "$order_id"
  },
  "GetShopConnectionStatus": {
    "shop_id": "$shop_id"
  },
  "GetShopSyncStatus": {
    "shop_id": "$shop_id"
  },
  "GetWorkerTask": {
    "shop_id": "$shop_id",
    "order_id": "$order_id"
  }
}
```

**expected_diagnosis**

```json
{
  "statuses": [
    "pending_human"
  ],
  "diagnosis_any": [
    "映射",
    "SKU_MAPPING_MISSING",
    "mapping"
  ],
  "expected_action": null
}
```

**expected_handoff**

```json
true
```

**current_label_sources**

```json
{
  "dataset": "evals/smoke.jsonl",
  "definition": "evals/dataset.py:WORKFLOW_CASES",
  "declared_claim_source": "human-authored scenario labels and initial database snapshot",
  "source_paths": [],
  "declared_business_truth_source": "simulator/lab/scenarios.py and simulator database facts",
  "fixture": "missing_sku_mapping"
}
```

## flow-info

**question**

有一笔订单没有同步，请调查

**expected_answer_or_required_facts**

```json
{
  "expected_result": {
    "statuses": [
      "needs_info"
    ],
    "diagnosis_any": [
      "店铺",
      "shop",
      "订单",
      "order"
    ],
    "truth_source": "simulator/lab/scenarios.py and simulator database facts"
  },
  "required_facts": [
    "店铺",
    "shop",
    "订单",
    "order"
  ]
}
```

**expected_documents**

```json
[]
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
  "statuses": [
    "needs_info"
  ],
  "diagnosis_any": [
    "店铺",
    "shop",
    "订单",
    "order"
  ],
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
  "definition": "evals/dataset.py:WORKFLOW_CASES",
  "declared_claim_source": "human-authored scenario labels and initial database snapshot",
  "source_paths": [],
  "declared_business_truth_source": "simulator/lab/scenarios.py and simulator database facts",
  "fixture": "missing_identifiers"
}
```

## flow-human

**question**

请转人工工程师

**expected_answer_or_required_facts**

```json
{
  "expected_result": {
    "statuses": [
      "pending_human"
    ],
    "diagnosis_any": [
      "engineer",
      "工程师"
    ],
    "truth_source": "simulator/lab/scenarios.py and simulator database facts"
  },
  "required_facts": [
    "engineer",
    "工程师"
  ]
}
```

**expected_documents**

```json
[]
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
  "statuses": [
    "pending_human"
  ],
  "diagnosis_any": [
    "engineer",
    "工程师"
  ],
  "expected_action": null
}
```

**expected_handoff**

```json
true
```

**current_label_sources**

```json
{
  "dataset": "evals/smoke.jsonl",
  "definition": "evals/dataset.py:WORKFLOW_CASES",
  "declared_claim_source": "human-authored scenario labels and initial database snapshot",
  "source_paths": [],
  "declared_business_truth_source": "simulator/lab/scenarios.py and simulator database facts",
  "fixture": "human_request"
}
```

## flow-transfer

**question**

shop-a 的订单 O-NOT-EXIST 还没有同步，请查询当前后台状态

**expected_answer_or_required_facts**

```json
{
  "expected_result": {
    "statuses": [
      "support_transfer"
    ],
    "diagnosis_any": [
      "支持",
      "Support",
      "后台"
    ],
    "truth_source": "simulator/lab/scenarios.py and simulator database facts"
  },
  "required_facts": [
    "支持",
    "Support",
    "后台"
  ]
}
```

**expected_documents**

```json
[]
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
  "statuses": [
    "support_transfer"
  ],
  "diagnosis_any": [
    "支持",
    "Support",
    "后台"
  ],
  "expected_action": null
}
```

**expected_handoff**

```json
true
```

**current_label_sources**

```json
{
  "dataset": "evals/smoke.jsonl",
  "definition": "evals/dataset.py:WORKFLOW_CASES",
  "declared_claim_source": "human-authored scenario labels and initial database snapshot",
  "source_paths": [],
  "declared_business_truth_source": "simulator/lab/scenarios.py and simulator database facts",
  "fixture": "customer_backend"
}
```
