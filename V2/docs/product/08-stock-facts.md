---
title: 库存事实与发布时间窗口
company_id: company-a
product: merchant-console
version: "2.0"
effective_from: 2026-01-01
effective_to: null
---
# 库存事实与发布时间窗口

本期只有一条库存规则：

```text
expected listed quantity = max(physical quantity - reserved quantity - safety stock, 0)
```

正常示例：Warehouse 实物 80、占用 10，Merchant 安全保留 5，Platform 应接收 65。发布任务保存 Warehouse 来源版本和稳定 request ID，Platform 保存接收版本。

Support Agent 只能调用 `GetStockFacts`。工具先读取 Merchant 商品关系，再读取 Warehouse 和 Platform，并由普通代码比较版本与时间：

- 同版本且数量为 65：符合规则。
- 来源版本较新但仍在 30 秒传播窗口：等待，不立即认定异常。
- 超过窗口仍是旧版本：确认版本未发布。
- 商品关系、版本或观测时间缺失：信息不足，不进行数字比较。

Support Agent 没有设置、调整或发布库存的工具。库存异常在 Phase 7 只解释或升级，不自动修复。
