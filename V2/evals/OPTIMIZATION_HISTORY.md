# ResolveAI Optimization History

以下为真实模型的 Quick Evaluation 观察，仅用于记录优化与面试素材，不能当作完整 Benchmark。各轮均使用 DeepSeek V4 Flash；前四轮使用相同五例：flow-order、flow-stock、flow-auth、flow-outage、flow-mapping，第五轮验证全部 11 个 Workflow Cases。前三轮 Ground Truth 未修改；第四轮按用户确认修正 flow-order 的合法动作范围，并补充 flow-outage 的关键声明检查。

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

## Optimization 5

Problem:
授权故障回答把商家订单记录中的空 merchant_sku 误说成 SKU 映射缺失。

Change:
明确空字段只说明该记录没有值，商家订单 SKU 不代表映射表，映射缺失必须有明确后端证据。

Result:
flow-auth 上轮的无映射表述本轮未出现且通过，但仍尝试重复查询连接状态；完整 11 例通过 9 例，Task Success 81.82%、Diagnosis 90.91%，flow-shipment 因缺少平台证据转人工、flow-worker 因额外仓库查询失败，Error / Timeout 为 0，真实运行 20261003T060758Z-c9dc9046（2026-10-03），费用 $0.003871112，这些 11 例指标不能与此前五例直接当作前后优化对照。

Why:
区分字段来源和明确错误码，使授权阻塞的回答不再把缺失的订单字段扩展为映射结论，本轮单次观察不保证以后永不复现。

## Optimization 6

Problem:
发货调查漏查平台就下结论并转人工，Worker 已确认中断后仍查仓库，授权调查还重复查询连接。

Change:
优先读取三方发货证据并限制平台结论，在订单、任务及连接证据一致时结束 Worker 或授权阻塞的新增读取。

Result:
相同 11 例通过 9 → 10，Task Success 81.82% → 90.91%、Tool Selection / Argument 81.82% → 100%，Diagnosis / Handoff 均维持 90.91%，shipment、worker、auth 通过，但 transfer 缺少 decision 字段导致 HTTP 409，order 重复请求及 shipment 的平台故障猜测仍在，先行四例另有 stock 模型超时，运行 20261003T074426Z-1804da1e（2026-10-03），本轮已确认费用 $0.0048070176，另保留未知费用预留 $0.000727832。

Why:
关键平台证据先读，原因已被匹配证据确认后停止扩展调查，减少漏查和无关工具请求，但没有证明整体已经稳定。

## Optimization 7

Problem:
Customer 交接字段不匹配导致崩溃，发货回答猜测责任方，订单重复或无关查询耗尽预算，Worker 指引误拒绝可重试的中断任务。

Change:
兼容明确 handoff 的字段别名，复用已有查询，证据完整后停止无关调查，并依据真实 retryable 判断、限制无证据的原因猜测。

Result:
两次完整 11 例均为 10/11，分别因 worker 错误转人工和 order 无关查询失败；修复后的三例回归为 3/3、五项指标均 100%，不能合并为完整 11/11。最新完整 11 例五项指标均为 90.91%，Error / Timeout 均为 0；worker 仍猜测系统故障或资源问题。费用 $0.008603672，未修改旧标签或新增 Cases。

Why:
复用证据并按真实业务条件判断，减少错误交接和多余调查，但局部通过尚不能证明整体稳定或所有回答都有依据。

## Optimization 8

Problem:
Worker 回答把中断状态进一步猜成没有证据支持的系统故障或资源问题。

Change:
要求具体原因由证据直接支持；没有根因证据时不列备选原因，只说明目前无法确认，规则覆盖最终回答和人工交接。

Result:
唯一一次完整 11 例为 10/11，Task Success、Tool Selection、Tool Argument 均为 90.91%，Diagnosis 和 Handoff 从上次完整运行的 90.91% 升至 100%，Error / Timeout 均为 0。Worker 的系统故障或资源猜测已消失，但因未主动查询标签要求的 GetOrderProcessRecords 而未通过；outage 和 mapping 仍列举无证据的可能原因，尚不符合稳定条件。未发现相同参数的重复调查或明显无关工具，费用 $0.003664528，运行 20261003T085039Z-71faa0dc（2026-10-03）。

Why:
把已观察到的错误状态与尚未证实的根因分开，改善了 Worker 表述，但一次运行也显示规则未被所有场景完全遵守。

## Optimization 9

Problem:
outage 和 mapping 在未知信息中列举没有证据的具体原因，Worker 标签还要求调查阶段重复查询计划层会独立校验的订单处理记录。

Change:
统一约束正常回答和转人工内容中的原因表述，评估增加独立的轻量原因检查；Worker 必选工具改为 GetWorkerTask，订单处理查询仍由计划层校验。

Result:
唯一一次完整 11 例从 10/11 到 11/11，Task Success、Tool Selection、Tool Argument 从 90.91% 到 100%，Diagnosis 和 Handoff 保持 100%，Error / Timeout 均为 0。最终回答未发现无依据具体原因或明显重复、无关调用，outage 原始输出中的网络故障和平台维护猜测已被输出层拦下。本轮 Worker 也主动查询了订单处理记录，按旧标签仍通过，不能把变化单独归因于标签修正。费用 $0.0033696712，运行 20261003T092528Z-9193cc26（2026-10-03）；现有 11 例达到停止优化条件，未新增 Cases。

Why:
在最终展示时区分业务状态和未证实的根因，并让调查标签符合真实业务前置条件，减少无依据解释和不必要的必选查询。

## Optimization 10

Problem:
新场景中，已完成订单仍调查发货，来源缺失只口头建议人工，未付款回答还承诺后续自动处理。

Change:
真实订单字段完整匹配后结束导入调查，必要来源缺失时实际转人工，禁止没有依据的自动处理承诺。

Result:
已完成订单由失败变为通过，调查读取从 6 次降至最新 2 次；来源缺失已实际创建人工工单。Development 初始原评分 4/6，修正合法等待路径后原输出离线评分为 5/6，最新完成结果仍为 5/6；最终验证保留 429 错误，共 7 次尝试，Passed 5 / Failed 1 / Error 1。Holdout 仅首次运行，自动评分 4/4，但证据复核发现平台版本更新场景的因果解释错误，不能宣称真实 4/4。旧 11 例未运行，本轮真实模型费用 $0.011083304，2026-10-03。

Why:
把任务完成、来源不足和后续处理条件分开，减少无关调查和虚假完成；未投递订单的合法恢复仍会漏选，新增场景尚未全部稳定。

## Optimization 11

Problem:
未接收订单会只建议恢复，已有足够订单证据仍多查发货，库存版本差异还会被解释成没有证据的发布原因。

Change:
把明确恢复意图接入真实待确认方案和人工工单，证据完整或明确冲突时结束导入调查，正常回答与人工交接统一保留事实并过滤未证实原因。

Result:
同一批新 Development 从 3/5 到 5/5，Task Success、Tool Selection、Tool Argument 从 60% 到 100%，Diagnosis 和 Handoff 保持 100%；订单冲突调查从 6 次读取降到 2 次，旧未投递订单已创建真实恢复方案。全新 Holdout 只运行一次，2/4 通过，五项指标依次为 50% / 75% / 50% / 50% / 75%（Task / Diagnosis / Tool Selection / Tool Argument / Handoff）；漏查处理记录和否定人工请求被误识别的问题原样保留，未针对 Holdout 修改。旧库存回归仍因诊断关键词未命中而失败。共 12 个不同 Case、18 次执行，Error / Timeout / Provider Error 均为 0；费用 $0.0071568，2026-10-03。这些是 Quick 优化记录，不是正式 Benchmark。

Why:
用真实方案和工单确认处理已提出，并在关键证据足够时停止无关调查；新 Holdout 仍暴露读取顺序与用户否定意图处理的泛化问题。

## Optimization 12

Problem:
否定人工请求被关键词误触发，关键查询被辅助读取挤占，已有足够证据仍继续调查。

Change:
统一识别明确拒绝、暂缓与条件式意图，先查决定结论的事实，证据一致或冲突时停止无关读取，并让可选工具与已有去重规则一致。

Result:
原已看过的 4 例从首次 2/4 到本轮 4/4；加入 2 个稳定案例的两次检查均为 6/6，五项指标均为 100%。全新 Holdout 仅首次运行，原始自动评分为 2/4，Task / Diagnosis / Tool Selection / Tool Argument / Handoff 为 50% / 75% / 50% / 50% / 100%，Error / Timeout 均为 0；仍有辅助查询和条件式请求过早交人工，自动通过的库存回答也有占用与安全保留的表述失真。10 个不同 Case 共 16 次执行，真实 OpenRouter 费用 $0.007265216，2026-10-03；首次结果未重跑或改分，不是正式 Benchmark。

Why:
限定请求范围与核心证据减少了已知场景的误操作和遗漏，但新题显示意图边界、诊断停止条件和事实表述仍未完全推广。

## Optimization 13

Problem:
发货调查误查订单 Worker，用户只要事实仍查询恢复条件，来源不存在或订单取消后仍扩展调查。

Change:
按请求范围暴露工具，尊重拒绝恢复的意图，关键业务事实确认后停止读取；简单缺失或不符合条件的结果直接返回结构化内容。

Before:
40 例 Baseline 中这 4 例均因无关工具失败。

After:
相关验证中这 4 例全部通过；最新 8 例为 7/8，剩余 1 例是库存诊断表述，没有 Error / Timeout。

Why:
减少无关调查和终止输出错误，依据业务事实完成请求，不依赖 Case ID。

## Optimization 14

Problem:
主模型限流后旧备用端点仍失败；新备用模型的必需推理配置不兼容，默认推理也造成超时。

Change:
限流时使用已配置的可用备用模型，使用它支持的较低推理强度；每次调用仍最多一次恢复。

Before:
Baseline 10/40 为 Provider Error；第一组相关验证有 2 个 Error 和 1 个 Timeout。

After:
最新相关 8 例 Error / Timeout 均为 0，5 次备用调用完成；尚需完整 Development 确认。

Why:
避免在已知不可用端点重复请求，兼容真实供应商要求，保留错误和实际费用。

## Optimization 15

Problem:
无依据原因过滤也删除了后端已确认的库存版本诊断，导致事实和方案正确但 Diagnosis 失败。

Change:
保留成功库存读取返回的 VERSION_NOT_PUBLISHED 诊断代码，继续把更深层原因标为未知。

Before:
最新相关验证中 flow-stock 的 Diagnosis 失败，其余四项通过。

After:
该例加 4 个相关回归为 5/5，五项指标均为 100%，Error / Timeout 均为 0。

Why:
区分真实业务诊断与没有证据的根因，不修改 Ground Truth。
