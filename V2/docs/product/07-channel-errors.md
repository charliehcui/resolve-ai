---
title: 渠道错误与重新授权
company_id: company-a
product: merchant-console
version: "2.0"
effective_from: 2026-01-01
effective_to: null
---
# 渠道错误与重新授权

每个店铺都有独立的渠道和连接状态。订单导入、发货回传和库存发布只读取当前店铺状态；某个店铺出现 401、403、429、500、503 或 timeout，不会改变其他店铺或公司的任务。

- 401：授权已过期，需要测试操作者模拟重新授权。
- 403：当前授权无权执行该操作，Support 只能说明并升级。
- 429：渠道限流，本次任务保留失败事实和请求编号。
- 500/503：渠道内部错误或暂时不可用，不当作空数据。
- timeout：结果不可确认，不推断成功。

只有本地测试操作者能运行 `connection restore`。Support Agent 只有 `CheckConnection` 只读工具，普通商家也没有修改连接状态的接口。测试环境不会收集或自动生成真实平台授权凭据。
