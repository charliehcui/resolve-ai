import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

const API = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000/api/v1";

async function call(path, token, options = {}) {
  const response = await fetch(`${API}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}`, ...(options.headers || {}) },
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
  return body;
}

function JsonCard({ title, value }) {
  if (!value || (Array.isArray(value) && value.length === 0)) return null;
  return <section><h2>{title}</h2><pre>{JSON.stringify(value, null, 2)}</pre></section>;
}

function App() {
  const [token, setToken] = useState("");
  const [engineerToken, setEngineerToken] = useState("");
  const [conversationId, setConversationId] = useState("");
  const [question, setQuestion] = useState("");
  const [snapshot, setSnapshot] = useState(null);
  const [tickets, setTickets] = useState([]);
  const [engineerTicket, setEngineerTicket] = useState(null);
  const [error, setError] = useState("");

  const run = async (work) => {
    setError("");
    try { await work(); } catch (e) { setError(e.message); }
  };
  const start = () => run(async () => {
    const data = await call("/conversations", token, { method: "POST" });
    setConversationId(data.conversation.conversation_id);
    setSnapshot(data);
  });
  const send = () => run(async () => {
    const data = await call(`/conversations/${conversationId}/messages`, token, { method: "POST", body: JSON.stringify({ question }) });
    setSnapshot(data.snapshot);
    setQuestion("");
  });
  const requestHuman = () => run(async () => {
    await call("/tickets", token, { method: "POST", body: JSON.stringify({ conversation_id: conversationId, reason: "User requested engineer support from the demo UI" }) });
    setSnapshot(await call(`/conversations/${conversationId}`, token));
  });
  const decide = (actionId, decision) => run(async () => {
    await call(`/actions/${actionId}/decision`, token, { method: "POST", body: JSON.stringify({ decision }) });
    setSnapshot(await call(`/conversations/${conversationId}`, token));
  });
  const execute = (actionId) => run(async () => {
    await call(`/actions/${actionId}/execute`, token, { method: "POST" });
    setSnapshot(await call(`/conversations/${conversationId}`, token));
  });
  const loadEngineer = () => run(async () => setTickets(await call("/engineer/tickets", engineerToken)));
  const openTicket = (id) => run(async () => setEngineerTicket(await call(`/tickets/${id}`, engineerToken)));
  const recheckTicket = () => run(async () => {
    const updated = await call(`/tickets/${engineerTicket.ticket_id}/recheck`, engineerToken, { method: "POST" });
    setEngineerTicket(updated);
    setTickets(await call("/engineer/tickets", engineerToken));
    if (conversationId) setSnapshot(await call(`/conversations/${conversationId}`, token));
  });

  return <main>
    <header><p className="eyebrow">Evidence-driven merchant support</p><h1>ResolveAI V2</h1><p>One real data flow from conversation to investigation, approval, verification, and Engineer Ticket.</p></header>
    {error && <div className="error">{error}</div>}
    <div className="columns">
      <div>
        <section><h2>Merchant conversation</h2><label>Merchant access token<input type="password" value={token} onChange={(e) => setToken(e.target.value)} /></label><div className="row"><button onClick={start}>Start conversation</button><span className="mono">{conversationId || "No conversation"}</span></div><textarea placeholder="Ask a question or request human support" value={question} onChange={(e) => setQuestion(e.target.value)} /><div className="row"><button disabled={!conversationId || !question} onClick={send}>Send</button><button className="secondary" disabled={!conversationId} onClick={requestHuman}>Request engineer</button></div></section>
        {snapshot && <section><div className="role">Current Agent: {snapshot.conversation.active_role}</div><h2>Conversation</h2>{snapshot.messages.map((message, index) => <article key={index}><strong>{message.role}</strong><p>{message.content}</p>{message.metadata?.citations?.length > 0 && <small>Citations: {message.metadata.citations.map((item) => item.title).join(", ")}</small>}</article>)}</section>}
        <JsonCard title="Investigation Evidence" value={snapshot?.case?.evidence} />
        {snapshot?.actions?.map((action) => <section key={action.action_id}><h2>Proposal / Approval / Verification</h2><pre>{JSON.stringify(action, null, 2)}</pre><div className="row"><button onClick={() => decide(action.action_id, "approve")}>Approve</button><button className="secondary" onClick={() => decide(action.action_id, "reject")}>Reject</button><button onClick={() => execute(action.action_id)}>Execute &amp; verify</button></div></section>)}
        <JsonCard title="Engineer Ticket" value={snapshot?.ticket} />
      </div>
      <aside><section><h2>Engineer queue</h2><label>Engineer access token<input type="password" value={engineerToken} onChange={(e) => setEngineerToken(e.target.value)} /></label><button onClick={loadEngineer}>Load assigned tickets</button>{tickets.map((ticket) => <button className="ticket" key={ticket.ticket_id} onClick={() => openTicket(ticket.ticket_id)}>{ticket.status} · {ticket.category}: {ticket.customer_problem}</button>)}</section>{engineerTicket && <section><h2>Assigned Ticket</h2><div className={`ticket-status ${engineerTicket.status}`}>Ticket status: {engineerTicket.status}</div><pre>{JSON.stringify(engineerTicket, null, 2)}</pre><button onClick={recheckTicket}>Recheck business result</button></section>}</aside>
    </div>
  </main>;
}

createRoot(document.getElementById("root")).render(<App />);
