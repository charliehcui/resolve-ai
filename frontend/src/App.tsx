import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

import { getBackendHealth, getTicket, sendCustomerMessage } from "./lib/backend";
import type { SupportResponse, TicketResponse } from "./lib/backend";

type ChatMessage = {
  role: "customer" | "assistant";
  content: string;
};

const statusLabels: Record<SupportResponse["status"], string> = {
  started: "正在了解问题",
  waiting_for_customer: "等待你的回复",
  waiting_for_verification: "等待确认结果",
  resolved: "问题已解决",
  unresolved: "需要进一步处理",
  needs_assistance: "已转交技术支持",
  support_resolved: "技术支持已给出结论",
  engineer_escalation: "工程师继续检查",
};

type SupportTicketViewProps = {
  ticket: TicketResponse | null;
  isLoading: boolean;
  errorMessage: string;
};

function SupportTicketView({ ticket, isLoading, errorMessage }: SupportTicketViewProps) {
  if (isLoading) {
    return <p className="mt-10 text-sm text-slate-400">Loading support investigation result...</p>;
  }

  if (errorMessage) {
    return <p role="alert" className="mt-10 rounded-xl border border-red-900 bg-red-950 px-4 py-3 text-sm text-red-200">{errorMessage}</p>;
  }

  if (ticket === null) {
    return <p className="mt-10 rounded-2xl border border-slate-800 bg-slate-900 p-6 text-sm text-slate-400">The current session has not created a ticket.</p>;
  }

  const handoff = ticket.handoff;
  const investigation = ticket.investigation_result;
  const resultLabel = investigation?.outcome === "resolution" ? "Resolution" : "Engineer escalation";

  return (
    <div className="pb-10">
      <div className="mb-7 mt-8">
        <p className="text-xs font-semibold tracking-widest text-cyan-400">SUPPORT INVESTIGATION</p>
        <h1 className="mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">Ticket #{ticket.id}</h1>
        <p className="mt-3 text-slate-400">This view shows the structured handoff and investigation result. Hidden reasoning is never displayed.</p>
      </div>

      <div className="grid items-start gap-6 lg:grid-cols-2">
        <section className="rounded-2xl border border-slate-800 bg-slate-900 p-6">
          <h2 className="text-lg font-semibold">Customer handoff</h2>
          {handoff === null ? (
            <p className="mt-4 text-sm text-slate-400">This ticket has no handoff data.</p>
          ) : (
            <div className="mt-5 space-y-5 text-sm">
              <div>
                <p className="text-xs text-slate-500">Issue</p>
                <p className="mt-2 leading-6 text-slate-200">{handoff.issue_summary}</p>
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <p className="text-xs text-slate-500">Affected feature</p>
                  <p className="mt-2 text-slate-300">{handoff.affected_feature}</p>
                </div>
                <div>
                  <p className="text-xs text-slate-500">Approximate start time</p>
                  <p className="mt-2 text-slate-300">{handoff.approximate_start_time ?? "Unknown"}</p>
                </div>
              </div>
              <div>
                <p className="text-xs text-slate-500">Customer impact</p>
                <p className="mt-2 leading-6 text-slate-300">{handoff.customer_impact}</p>
              </div>
              <div>
                <p className="text-xs text-slate-500">Handoff reason</p>
                <p className="mt-2 leading-6 text-slate-300">{handoff.handoff_reason}</p>
              </div>
              <div>
                <p className="text-xs text-slate-500">Collected facts</p>
                {handoff.collected_facts.length === 0 ? (
                  <p className="mt-2 text-slate-400">No customer-side facts were collected.</p>
                ) : (
                  <ul className="mt-3 space-y-2">
                    {handoff.collected_facts.map((fact) => (
                      <li key={`${fact.name}-${fact.source}`} className="rounded-xl border border-slate-800 bg-slate-950 p-3">
                        <p className="text-slate-200">{fact.name}: {fact.value}</p>
                        <p className="mt-1 text-xs text-slate-500">Source: {fact.source}</p>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <div>
                <p className="text-xs text-slate-500">Environment snapshot</p>
                {Object.keys(handoff.environment_snapshot).length === 0 ? (
                  <p className="mt-2 text-slate-400">No environment snapshot was saved.</p>
                ) : (
                  <dl className="mt-3 space-y-2">
                    {Object.entries(handoff.environment_snapshot).map(([name, value]) => (
                      <div key={name} className="flex flex-wrap justify-between gap-2 rounded-xl border border-slate-800 bg-slate-950 p-3">
                        <dt className="text-slate-400">{name}</dt>
                        <dd className="break-all text-slate-200">{typeof value === "object" ? JSON.stringify(value) : String(value)}</dd>
                      </div>
                    ))}
                  </dl>
                )}
              </div>
              <div>
                <p className="text-xs text-slate-500">Attempted steps</p>
                {handoff.attempted_steps.length === 0 ? (
                  <p className="mt-2 text-slate-400">No steps have been attempted.</p>
                ) : (
                  <ol className="mt-3 list-decimal space-y-2 pl-5 text-slate-300">
                    {handoff.attempted_steps.map((step) => <li key={step}>{step}</li>)}
                  </ol>
                )}
              </div>
              {handoff.citation_ids.length > 0 && (
                <div>
                  <p className="text-xs text-slate-500">Citation IDs</p>
                  <ul className="mt-3 list-disc space-y-2 pl-5 text-slate-300">
                    {handoff.citation_ids.map((citationId) => <li key={citationId} className="break-all">{citationId}</li>)}
                  </ul>
                </div>
              )}
              {handoff.remaining_questions.length > 0 && (
                <div>
                  <p className="text-xs text-slate-500">Remaining questions</p>
                  <ul className="mt-3 list-disc space-y-2 pl-5 text-slate-300">
                    {handoff.remaining_questions.map((question) => <li key={question}>{question}</li>)}
                  </ul>
                </div>
              )}
            </div>
          )}
        </section>

        <div className="space-y-6">
          <section className="rounded-2xl border border-slate-800 bg-slate-900 p-6">
            <h2 className="text-lg font-semibold">Tools used</h2>
            {ticket.investigation_tools === null || ticket.investigation_tools.length === 0 ? (
              <p className="mt-4 text-sm text-slate-400">No internal tool result was recorded.</p>
            ) : (
              <ul className="mt-4 space-y-2">
                {ticket.investigation_tools.map((toolName) => (
                  <li key={toolName} className="rounded-xl border border-slate-800 bg-slate-950 px-4 py-3 text-sm text-cyan-300">{toolName}</li>
                ))}
              </ul>
            )}
          </section>

          <section className="rounded-2xl border border-slate-800 bg-slate-900 p-6">
            <h2 className="text-lg font-semibold">Investigation result</h2>
            {investigation === null ? (
              <p className="mt-4 text-sm text-slate-400">No investigation result has been saved.</p>
            ) : (
              <div className="mt-5 space-y-5">
                <div>
                  <p className="text-xs text-slate-500">Outcome</p>
                  <p className="mt-2 text-sm font-medium text-cyan-300">{resultLabel}</p>
                </div>
                <div>
                  <p className="text-xs text-slate-500">Conclusion</p>
                  <p className="mt-2 text-sm leading-6 text-slate-200">{investigation.conclusion}</p>
                </div>
                <div>
                  <p className="text-xs text-slate-500">Supporting facts</p>
                  {investigation.supporting_facts.length === 0 ? (
                    <p className="mt-2 text-sm text-slate-400">No supporting tool facts were recorded.</p>
                  ) : (
                    <ul className="mt-3 list-disc space-y-2 pl-5 text-sm leading-6 text-slate-300">
                      {investigation.supporting_facts.map((fact) => <li key={fact}>{fact}</li>)}
                    </ul>
                  )}
                </div>
                <div>
                  <p className="text-xs text-slate-500">Customer-visible explanation</p>
                  <p className="mt-2 rounded-xl border border-slate-800 bg-slate-950 p-4 text-sm leading-6 text-slate-300">{investigation.customer_explanation}</p>
                </div>
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}

function App() {
  const [healthLabel, setHealthLabel] = useState("正在检查连接");
  const [isReady, setIsReady] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [message, setMessage] = useState("");
  const [support, setSupport] = useState<SupportResponse | null>(null);
  const [view, setView] = useState<"customer" | "support">("customer");
  const [ticket, setTicket] = useState<TicketResponse | null>(null);
  const [isLoadingTicket, setIsLoadingTicket] = useState(false);
  const [ticketError, setTicketError] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const conversationEnd = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let active = true;

    async function checkBackend() {
      const health = await getBackendHealth();

      if (!active) {
        return;
      }

      setIsReady(health.isReady);

      if (health.isReady) {
        setHealthLabel("连接正常");
      } else {
        setHealthLabel("连接不可用");
      }
    }

    void checkBackend();

    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    conversationEnd.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, isSending]);

  let sessionFinished = false;

  if (support !== null) {
    sessionFinished = support.status === "resolved" || support.status === "unresolved" || support.status === "needs_assistance" || support.status === "support_resolved" || support.status === "engineer_escalation";
  }

  let currentStatus = "可以开始咨询";

  if (support !== null) {
    currentStatus = statusLabels[support.status];
  }

  if (isSending) {
    currentStatus = "正在处理你的消息";
  }

  let statusStyle = "border-slate-700 bg-slate-800 text-slate-300";

  if (support?.status === "resolved" || support?.status === "support_resolved") {
    statusStyle = "border-emerald-800 bg-emerald-950 text-emerald-300";
  } else if (support?.status === "unresolved" || support?.status === "needs_assistance" || support?.status === "engineer_escalation") {
    statusStyle = "border-amber-800 bg-amber-950 text-amber-300";
  } else if (isSending || support?.status === "waiting_for_verification") {
    statusStyle = "border-cyan-800 bg-cyan-950 text-cyan-300";
  }

  let connectionStyle = "bg-amber-400";

  if (isReady) {
    connectionStyle = "bg-emerald-400";
  }

  let visibleHealthLabel = healthLabel;

  if (view === "support") {
    visibleHealthLabel = isReady ? "Connected" : "Unavailable";
  }

  let inputPlaceholder = "请描述哪里不能正常使用，不需要提供技术细节。";

  if (support?.status === "waiting_for_verification") {
    inputPlaceholder = "请告诉我们建议步骤是否解决了问题。";
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const customerMessage = message.trim();

    if (!customerMessage || isSending || sessionFinished) {
      return;
    }

    let sessionId: string | null = null;

    if (support !== null) {
      sessionId = support.session_id;
    }

    setIsSending(true);
    setErrorMessage("");

    try {
      const response = await sendCustomerMessage(sessionId, customerMessage);
      const customerEntry: ChatMessage = { role: "customer", content: customerMessage };
      const assistantEntry: ChatMessage = { role: "assistant", content: response.customer_response };

      setMessages((currentMessages) => [...currentMessages, customerEntry, assistantEntry]);
      setSupport(response);
      setMessage("");
      setIsReady(true);
      setHealthLabel("连接正常");

      if (response.ticket_id !== null) {
        setIsLoadingTicket(true);
        setTicketError("");

        try {
          const savedTicket = await getTicket(response.ticket_id);
          setTicket(savedTicket);
        } catch (ticketLoadError) {
          if (ticketLoadError instanceof Error) {
            setTicketError(ticketLoadError.message);
          } else {
            setTicketError("The ticket could not be loaded. Please try again.");
          }
        } finally {
          setIsLoadingTicket(false);
        }
      }
    } catch (error) {
      if (error instanceof Error) {
        setErrorMessage(error.message);
      } else {
        setErrorMessage("消息发送失败，请重试。");
      }
    } finally {
      setIsSending(false);
    }
  }

  function startNewConversation() {
    if (isSending) {
      return;
    }

    setMessages([]);
    setMessage("");
    setSupport(null);
    setView("customer");
    setTicket(null);
    setTicketError("");
    setErrorMessage("");
  }

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100">
      <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
        <header className="flex flex-wrap items-center justify-between gap-4 border-b border-slate-800 pb-6">
          <div className="flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-cyan-400 font-bold text-slate-950">
              R
            </div>
            <div>
              <p className="text-xl font-semibold tracking-tight">ResolveAI</p>
              <p className="text-sm text-slate-400">{view === "customer" ? "客户支持" : "Support investigation"}</p>
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-end gap-3">
            <div className="flex rounded-lg border border-slate-700 bg-slate-900 p-1">
              <button type="button" onClick={() => setView("customer")} className={`rounded-md px-3 py-1.5 text-sm ${view === "customer" ? "bg-cyan-400 font-semibold text-slate-950" : "text-slate-300 hover:bg-slate-800"}`}>
                {view === "customer" ? "客户视图" : "Customer View"}
              </button>
              <button type="button" onClick={() => setView("support")} className={`rounded-md px-3 py-1.5 text-sm ${view === "support" ? "bg-cyan-400 font-semibold text-slate-950" : "text-slate-300 hover:bg-slate-800"}`}>
                {view === "customer" ? "支持视图" : "Support View"}
              </button>
            </div>
            <span className="flex items-center gap-2 text-xs text-slate-400">
              <span className={`h-2 w-2 rounded-full ${connectionStyle}`} />
              {visibleHealthLabel}
            </span>
            <button type="button" onClick={startNewConversation} disabled={isSending} className="rounded-lg border border-slate-700 px-3 py-2 text-sm transition hover:border-slate-500 hover:bg-slate-900 focus-visible:outline-2 focus-visible:outline-cyan-400 disabled:cursor-not-allowed disabled:opacity-50">
              {view === "customer" ? "新建会话" : "New conversation"}
            </button>
          </div>
        </header>

        {view === "customer" ? (
          <>
        <div className="mb-7 mt-8">
          <p className="text-xs font-semibold tracking-widest text-cyan-400">一步一步解决问题</p>
          <h1 className="mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">我们来解决这个问题。</h1>
          <p className="mt-3 max-w-2xl leading-7 text-slate-400">
            请告诉我们发生了什么。我们会查看现有信息，并引导你完成下一步。
          </p>
        </div>

        <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
          <section aria-label="支持会话" className="min-w-0 overflow-hidden rounded-2xl border border-slate-800 bg-slate-900">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 px-5 py-4">
              <h2 className="font-semibold">你的会话</h2>
              <span role="status" className={`rounded-full border px-3 py-1 text-xs ${statusStyle}`}>
                {currentStatus}
              </span>
            </div>

            <div role="log" aria-live="polite" aria-label="会话消息" className="h-[50vh] min-h-80 overflow-y-auto px-5 py-6 sm:px-6">
              {messages.length === 0 && (
                <div className="mx-auto max-w-lg py-12 text-center">
                  <div className="mx-auto mb-5 flex h-12 w-12 items-center justify-center rounded-2xl border border-cyan-900 bg-cyan-950 text-xl text-cyan-300">
                    ?
                  </div>
                  <h3 className="text-lg font-medium">需要我们帮你解决什么问题？</h3>
                  <p className="mt-3 leading-7 text-slate-400">
                    请先说明你原本想做什么，以及实际发生了什么。
                  </p>
                  <button type="button" disabled={isSending} onClick={() => setMessage("我的订单通知从今天开始收不到了，我希望恢复接收。")} className="mt-6 rounded-xl border border-slate-700 px-4 py-3 text-sm text-slate-300 transition hover:border-cyan-700 hover:bg-slate-800 focus-visible:outline-2 focus-visible:outline-cyan-400 disabled:opacity-50">
                    我的订单通知收不到了
                  </button>
                </div>
              )}

              <div className="space-y-5">
                {messages.map((chatMessage, index) => {
                  let alignment = "justify-start";
                  let bubbleStyle = "border-slate-700 bg-slate-800 text-slate-200";
                  let author = "ResolveAI";

                  if (chatMessage.role === "customer") {
                    alignment = "justify-end";
                    bubbleStyle = "border-cyan-800 bg-cyan-950 text-cyan-50";
                    author = "你";
                  }

                  return (
                    <div key={index} className={`flex ${alignment}`}>
                      <article className={`max-w-[92%] rounded-2xl border px-4 py-3 sm:max-w-[85%] ${bubbleStyle}`}>
                        <p className="mb-2 text-xs font-semibold text-slate-400">{author}</p>
                        <p className="whitespace-pre-wrap break-words text-sm leading-7">{chatMessage.content}</p>
                      </article>
                    </div>
                  );
                })}

                {isSending && (
                  <div className="space-y-4">
                    <div className="flex justify-end">
                      <div className="max-w-[92%] rounded-2xl border border-cyan-800 bg-cyan-950 px-4 py-3 sm:max-w-[85%]">
                        <p className="mb-2 text-xs font-semibold text-slate-400">你</p>
                        <p className="whitespace-pre-wrap break-words text-sm leading-7">{message.trim()}</p>
                      </div>
                    </div>
                    <p className="text-sm text-slate-400">正在处理你的消息……</p>
                  </div>
                )}
              </div>

              <div ref={conversationEnd} />
            </div>

            <form onSubmit={handleSubmit} className="border-t border-slate-800 p-5">
              {errorMessage && (
                <p role="alert" className="mb-4 rounded-xl border border-red-900 bg-red-950 px-4 py-3 text-sm text-red-200">
                  {errorMessage}
                </p>
              )}

              {sessionFinished ? (
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <p className="text-sm text-slate-400">本次会话已结束。</p>
                  <button type="button" onClick={startNewConversation} className="rounded-lg bg-cyan-400 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-cyan-300 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan-400">
                    开始新的会话
                  </button>
                </div>
              ) : (
                <>
                  <label htmlFor="customer-message" className="mb-2 block text-sm font-medium text-slate-300">
                    你的消息
                  </label>
                  <textarea id="customer-message" value={message} onChange={(event) => setMessage(event.target.value)} placeholder={inputPlaceholder} rows={3} maxLength={5000} disabled={isSending} required className="w-full resize-y rounded-xl border border-slate-700 bg-slate-950 px-4 py-3 text-sm leading-6 outline-none placeholder:text-slate-500 focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500 disabled:opacity-60" />
                  <div className="mt-3 flex items-center justify-between gap-3">
                    <p className="text-xs text-slate-500">{message.length} / 5,000</p>
                    <button type="submit" disabled={isSending || !message.trim()} className="rounded-lg bg-cyan-400 px-5 py-2.5 text-sm font-semibold text-slate-950 transition hover:bg-cyan-300 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan-400 disabled:cursor-not-allowed disabled:opacity-40">
                      {isSending ? "正在发送……" : "发送消息"}
                    </button>
                  </div>
                </>
              )}
            </form>
          </section>

          <aside aria-label="支持详情" className="space-y-4">
            <section className="rounded-2xl border border-slate-800 bg-slate-900 p-5">
              <h2 className="font-semibold">我们了解到的问题</h2>
              {support === null ? (
                <p className="mt-3 text-sm leading-6 text-slate-400">发送第一条消息后，这里会显示问题总结。</p>
              ) : (
                <>
                  <p className="mt-3 text-sm leading-7 text-slate-300">{support.problem_details.summary}</p>
                  <p className="mt-4 text-xs font-medium tracking-wide text-slate-500">你的目标</p>
                  <p className="mt-2 text-sm leading-6 text-slate-400">{support.problem_details.customer_goal}</p>
                </>
              )}
            </section>

            <section className="rounded-2xl border border-slate-800 bg-slate-900 p-5">
              <h2 className="font-semibold">已收集的信息</h2>
              <p className="mt-2 text-xs leading-5 text-slate-500">系统在准备建议时读取的信息。</p>
              {support === null || support.customer_facts.length === 0 ? (
                <p className="mt-3 text-sm text-slate-400">暂时还没有收集到信息。</p>
              ) : (
                <ul className="mt-4 space-y-3">
                  {support.customer_facts.map((fact) => (
                    <li key={fact} className="flex gap-3 text-sm leading-6 text-slate-300">
                      <span aria-hidden="true" className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-cyan-400" />
                      <span className="break-words">{fact}</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="rounded-2xl border border-slate-800 bg-slate-900 p-5">
              <h2 className="font-semibold">参考资料</h2>
              {support === null || support.citations.length === 0 ? (
                <p className="mt-3 text-sm leading-6 text-slate-400">提供解决建议后，相关客户指南会显示在这里。</p>
              ) : (
                <ul className="mt-4 space-y-3">
                  {support.citations.map((citation) => (
                    <li key={citation.chunk_id} className="rounded-xl border border-slate-800 bg-slate-950 px-3 py-3">
                      <p className="break-all text-sm text-cyan-300">{citation.source_uri}</p>
                      <p className="mt-2 text-xs text-slate-500">版本 {citation.version}</p>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            {support?.ticket_id !== null && support?.ticket_id !== undefined && (
              <section className="rounded-2xl border border-amber-900 bg-amber-950 p-5">
                <h2 className="font-semibold text-amber-200">已转交技术支持</h2>
                <p className="mt-3 text-sm leading-6 text-amber-100">工单编号：#{support.ticket_id}</p>
                <p className="mt-2 text-sm leading-6 text-amber-200">已经收集的信息会随工单一起提交，你不需要重复说明。</p>
              </section>
            )}

            {support?.verification_source === "customer_confirmation" && (
              <section className="rounded-2xl border border-slate-700 bg-slate-900 p-5">
                <h2 className="font-semibold">你反馈的结果</h2>
                <p className="mt-3 text-sm leading-6 text-slate-400">已记录以下确认内容：</p>
                <blockquote className="mt-3 border-l-2 border-cyan-700 pl-3 text-sm leading-7 text-slate-200">
                  {support.verification_result?.supporting_text}
                </blockquote>
              </section>
            )}

            {support?.verification_source === "tool_verification" && (
              <section className="rounded-2xl border border-emerald-900 bg-emerald-950 p-5">
                <h2 className="font-semibold text-emerald-200">已确认恢复</h2>
                <p className="mt-3 text-sm leading-6 text-emerald-100">最新账户信息确认问题已经解决。</p>
              </section>
            )}
          </aside>
        </div>
          </>
        ) : (
          <SupportTicketView ticket={ticket} isLoading={isLoadingTicket} errorMessage={ticketError} />
        )}
      </div>
    </main>
  );
}

export default App;
