---
title: 库存上架数量规则
company_id: company-a
product: merchant-console
version: "2.0"
effective_from: 2026-01-01
effective_to: null
---
本期唯一库存规则是：应上架数 = max(实物库存 − 占用库存 − 安全保留, 0)。库存功能只提供事实解释和异常识别，不提供由 Agent 修改库存的操作。

