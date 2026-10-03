# ResolveAI Optimization History

以下为真实模型的 Quick Evaluation 观察，仅用于记录优化与面试素材，不能当作完整 Benchmark。各轮均使用 DeepSeek V4 Flash 和相同五例：flow-order、flow-stock、flow-auth、flow-outage、flow-mapping。前三轮 Ground Truth 未修改；第四轮按用户确认修正 flow-order 的合法动作范围，并补充 flow-outage 的关键声明检查。

## Optimization 1

Problem:
订单和库存调查混淆，证据编号被缩写，重复查询或预算耗尽导致已有结论丢失。

Change:
修正标识符识别和调查指引，并允许在停止读取后用已有证据做一次终止判断。

Result:
同样五例的 Task Success 20% → 60%、Diagnosis 20% → 80%、Tool Selection 60% → 80%、Tool Argument 60% → 80%、Handoff 60% → 100%；仍有两例失败，尚不稳定。

Why:
已有证据能保留到最终判断，减少调查范围错误和过早转人工；这五例显示改善，但尚不能证明整体效果。

## Optimization 2

Problem:
映射已确认缺失后仍查询库存，并把本地参数拒绝当成商品无效的业务事实。

Change:
只向模型提供当前交接编号能调用的工具，明确本地校验错误的含义，并排除它对业务事实的支持。

Result:
flow-mapping 未通过 → 通过，该例 Tool Selection 和 Tool Argument 均为 0% → 100%（单例）；五例总体 Task Success 60% → 60%、Diagnosis 80% → 80%、Tool Selection 80% → 80%、Tool Argument 80% → 80%、Handoff 100% → 100%；flow-outage 本次出现额外仓库查询而失败，整体尚不稳定。

Why:
模型不能正常调用缺少调查编号的库存工具，本地拒绝也不能再被引用为业务事实；本轮改善了映射案例，但没有证明总体提升。

## Optimization 3

Problem:
已有证据确认业务阻碍后仍继续调查其他领域，渠道故障案例因此额外查询仓库。

Change:
订单与处理记录确认映射阻塞，或渠道故障与处理失败一致时停止新增读取，保留 Agent 的最终判断。

Result:
五例 Task Success 60% → 80%、Diagnosis 80% → 80%、Tool Selection 80% → 100%、Tool Argument 80% → 100%、Handoff 100% → 100%；flow-mapping 再次通过，flow-outage 本轮通过，flow-order 保留动作标签歧义而失败；outage 回答预览中的自动重试表述尚无代码支持，当前规则分数不能证明全部语义正确。

Why:
业务阻碍已经确认时不再扩大读取，减少与问题无关的工具调用；本地参数错误不能触发停止条件。

## Optimization 4

Problem:
订单案例把合法的失败任务重试判错，渠道故障回答还承诺了代码没有实现的恢复后自动重试。

Change:
只让该订单场景按各自前置条件和真实证据接受两种重试动作，并明确禁止无依据自动重试承诺，在最终完整回答中检查这项声明。

Result:
Task Success 80% → 100%、Diagnosis 80% → 100%、Tool Selection 100% → 100%、Tool Argument 100% → 100%、Handoff 100% → 100%；五例均通过，Error / Timeout 为 0，outage 回答改为恢复后重新检查，本轮未出现自动重试承诺。订单得分提升属于修正过严标准，并非模型能力提升；新增声明检查也改变了评分口径，前后不能视为纯优化对照。真实 DeepSeek V4 Flash 一轮，无 Mock、无 fallback；费用 $0.002235604，运行编号 20261003T055321Z-e2b6093f（2026-10-03）。仅为五例 Quick Evaluation，未跑完整 Workflow Benchmark；声明规则不代表完整语义正确率；人工阅读另发现 flow-auth 把处理记录中的空 merchant_sku 表述为无 SKU 映射，两者不等价，超出本轮范围未修改，也未被现有规则扣分。

Why:
合法动作按业务条件判断，故障回答只描述已有证据支持的后续步骤，减少错误扣分和无依据承诺。
