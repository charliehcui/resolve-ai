import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

import { getBackendHealth, sendCustomerMessage } from "./lib/backend";
import type { SupportResponse } from "./lib/backend";

type ChatMessage = {
  role: "customer" | "assistant";
  content: string;
};

const statusLabels: Record<SupportResponse["status"], string> = {
  started: "Understanding your problem",
  waiting_for_customer: "Waiting for your reply",
  waiting_for_verification: "Waiting for your result",
  resolved: "Resolved",
  unresolved: "Further help needed",
  needs_assistance: "Further help needed",
};

function App() {
  const [healthLabel, setHealthLabel] = useState("Checking connection");
  const [isReady, setIsReady] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [message, setMessage] = useState("");
  const [support, setSupport] = useState<SupportResponse | null>(null);
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
        setHealthLabel("Connection ready");
      } else {
        setHealthLabel("Connection unavailable");
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
    sessionFinished = support.status === "resolved" || support.status === "unresolved" || support.status === "needs_assistance";
  }

  let currentStatus = "Ready to help";

  if (support !== null) {
    currentStatus = statusLabels[support.status];
  }

  if (isSending) {
    currentStatus = "Working on your message";
  }

  let statusStyle = "border-slate-700 bg-slate-800 text-slate-300";

  if (support?.status === "resolved") {
    statusStyle = "border-emerald-800 bg-emerald-950 text-emerald-300";
  } else if (support?.status === "unresolved" || support?.status === "needs_assistance") {
    statusStyle = "border-amber-800 bg-amber-950 text-amber-300";
  } else if (isSending || support?.status === "waiting_for_verification") {
    statusStyle = "border-cyan-800 bg-cyan-950 text-cyan-300";
  }

  let connectionStyle = "bg-amber-400";

  if (isReady) {
    connectionStyle = "bg-emerald-400";
  }

  let inputPlaceholder = "Describe what is not working. You do not need technical details.";

  if (support?.status === "waiting_for_verification") {
    inputPlaceholder = "Tell us whether the suggested steps fixed the problem.";
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
      setHealthLabel("Connection ready");
    } catch (error) {
      if (error instanceof Error) {
        setErrorMessage(error.message);
      } else {
        setErrorMessage("Your message could not be sent. Please try again.");
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
              <p className="text-sm text-slate-400">Customer support</p>
            </div>
          </div>

          <div className="flex items-center gap-4">
            <span className="flex items-center gap-2 text-xs text-slate-400">
              <span className={`h-2 w-2 rounded-full ${connectionStyle}`} />
              {healthLabel}
            </span>
            <button type="button" onClick={startNewConversation} disabled={isSending} className="rounded-lg border border-slate-700 px-3 py-2 text-sm transition hover:border-slate-500 hover:bg-slate-900 focus-visible:outline-2 focus-visible:outline-cyan-400 disabled:cursor-not-allowed disabled:opacity-50">
              New conversation
            </button>
          </div>
        </header>

        <div className="mb-7 mt-8">
          <p className="text-xs font-semibold uppercase tracking-widest text-cyan-400">A little help, step by step</p>
          <h1 className="mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">Let’s get this working.</h1>
          <p className="mt-3 max-w-2xl leading-7 text-slate-400">
            Tell us what happened. We will check the information available and help you work through the next steps.
          </p>
        </div>

        <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
          <section aria-label="Support conversation" className="min-w-0 overflow-hidden rounded-2xl border border-slate-800 bg-slate-900">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 px-5 py-4">
              <h2 className="font-semibold">Your conversation</h2>
              <span role="status" className={`rounded-full border px-3 py-1 text-xs ${statusStyle}`}>
                {currentStatus}
              </span>
            </div>

            <div role="log" aria-live="polite" aria-label="Conversation messages" className="h-[50vh] min-h-80 overflow-y-auto px-5 py-6 sm:px-6">
              {messages.length === 0 && (
                <div className="mx-auto max-w-lg py-12 text-center">
                  <div className="mx-auto mb-5 flex h-12 w-12 items-center justify-center rounded-2xl border border-cyan-900 bg-cyan-950 text-xl text-cyan-300">
                    ?
                  </div>
                  <h3 className="text-lg font-medium">What can we help you with?</h3>
                  <p className="mt-3 leading-7 text-slate-400">
                    Start with what you were trying to do and what happened instead.
                  </p>
                  <button type="button" disabled={isSending} onClick={() => setMessage("My order notifications stopped arriving today. I want to receive them again.")} className="mt-6 rounded-xl border border-slate-700 px-4 py-3 text-sm text-slate-300 transition hover:border-cyan-700 hover:bg-slate-800 focus-visible:outline-2 focus-visible:outline-cyan-400 disabled:opacity-50">
                    My order notifications are not arriving
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
                    author = "You";
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
                        <p className="mb-2 text-xs font-semibold text-slate-400">You</p>
                        <p className="whitespace-pre-wrap break-words text-sm leading-7">{message.trim()}</p>
                      </div>
                    </div>
                    <p className="text-sm text-slate-400">Working on your message…</p>
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
                  <p className="text-sm text-slate-400">This conversation has ended.</p>
                  <button type="button" onClick={startNewConversation} className="rounded-lg bg-cyan-400 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-cyan-300 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan-400">
                    Start another conversation
                  </button>
                </div>
              ) : (
                <>
                  <label htmlFor="customer-message" className="mb-2 block text-sm font-medium text-slate-300">
                    Your message
                  </label>
                  <textarea id="customer-message" value={message} onChange={(event) => setMessage(event.target.value)} placeholder={inputPlaceholder} rows={3} maxLength={5000} disabled={isSending} required className="w-full resize-y rounded-xl border border-slate-700 bg-slate-950 px-4 py-3 text-sm leading-6 outline-none placeholder:text-slate-500 focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500 disabled:opacity-60" />
                  <div className="mt-3 flex items-center justify-between gap-3">
                    <p className="text-xs text-slate-500">{message.length} / 5,000</p>
                    <button type="submit" disabled={isSending || !message.trim()} className="rounded-lg bg-cyan-400 px-5 py-2.5 text-sm font-semibold text-slate-950 transition hover:bg-cyan-300 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan-400 disabled:cursor-not-allowed disabled:opacity-40">
                      {isSending ? "Sending…" : "Send message"}
                    </button>
                  </div>
                </>
              )}
            </form>
          </section>

          <aside aria-label="Support details" className="space-y-4">
            <section className="rounded-2xl border border-slate-800 bg-slate-900 p-5">
              <h2 className="font-semibold">What we understand</h2>
              {support === null ? (
                <p className="mt-3 text-sm leading-6 text-slate-400">Your problem summary will appear here after your first message.</p>
              ) : (
                <>
                  <p className="mt-3 text-sm leading-7 text-slate-300">{support.problem_details.summary}</p>
                  <p className="mt-4 text-xs font-medium uppercase tracking-wide text-slate-500">Your goal</p>
                  <p className="mt-2 text-sm leading-6 text-slate-400">{support.problem_details.customer_goal}</p>
                </>
              )}
            </section>

            <section className="rounded-2xl border border-slate-800 bg-slate-900 p-5">
              <h2 className="font-semibold">Information collected</h2>
              <p className="mt-2 text-xs leading-5 text-slate-500">Information read while preparing your guidance.</p>
              {support === null || support.customer_facts.length === 0 ? (
                <p className="mt-3 text-sm text-slate-400">No information collected yet.</p>
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
              <h2 className="font-semibold">Sources</h2>
              {support === null || support.citations.length === 0 ? (
                <p className="mt-3 text-sm leading-6 text-slate-400">Relevant customer guides will appear here when we suggest a solution.</p>
              ) : (
                <ul className="mt-4 space-y-3">
                  {support.citations.map((citation) => (
                    <li key={citation.chunk_id} className="rounded-xl border border-slate-800 bg-slate-950 px-3 py-3">
                      <p className="break-all text-sm text-cyan-300">{citation.source_uri}</p>
                      <p className="mt-2 text-xs text-slate-500">Version {citation.version}</p>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            {support?.verification_source === "customer_confirmation" && (
              <section className="rounded-2xl border border-slate-700 bg-slate-900 p-5">
                <h2 className="font-semibold">Your reported result</h2>
                <p className="mt-3 text-sm leading-6 text-slate-400">Recorded from your confirmation:</p>
                <blockquote className="mt-3 border-l-2 border-cyan-700 pl-3 text-sm leading-7 text-slate-200">
                  {support.verification_result?.supporting_text}
                </blockquote>
              </section>
            )}

            {support?.verification_source === "tool_verification" && (
              <section className="rounded-2xl border border-emerald-900 bg-emerald-950 p-5">
                <h2 className="font-semibold text-emerald-200">Recovery confirmed</h2>
                <p className="mt-3 text-sm leading-6 text-emerald-100">The latest account information confirms that the problem is fixed.</p>
              </section>
            )}
          </aside>
        </div>
      </div>
    </main>
  );
}

export default App;
