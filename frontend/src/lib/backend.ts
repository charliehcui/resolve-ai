export type BackendHealth = {
  isReady: boolean;
  label: "正常" | "不可用";
};

type HealthResponse = {
  status?: string;
};

export type ProblemDetails = {
  summary: string;
  affected_feature: string;
  problem: string;
  customer_goal: string;
  missing_information: string[];
};

export type CustomerDocumentCitation = {
  chunk_id: string;
  source_uri: string;
  version: string;
};

export type CustomerResolution = {
  can_resolve: boolean;
  explanation: string;
  steps: string[];
  citation_ids: string[];
  verification_method: "customer_confirmation_or_tool";
};

export type CustomerVerification = {
  result: "resolved" | "unresolved" | "unclear";
  supporting_text: string;
};

export type SupportFact = {
  name: string;
  value: string;
  source: string;
};

export type SupportHandoff = {
  support_session_id: string;
  customer_id: string;
  issue_summary: string;
  affected_feature: string;
  customer_impact: string;
  approximate_start_time: string | null;
  environment_snapshot: Record<string, unknown>;
  collected_facts: SupportFact[];
  attempted_steps: string[];
  citation_ids: string[];
  remaining_questions: string[];
  handoff_reason: string;
};

export type SupportInvestigationResult = {
  conclusion: string;
  supporting_facts: string[];
  customer_explanation: string;
  outcome: "resolution" | "action_required" | "engineer_escalation";
};

export type TicketResponse = {
  id: number;
  support_session_id: string | null;
  handoff: SupportHandoff | null;
  investigation_result: SupportInvestigationResult | null;
  investigation_tools: string[] | null;
  status: "OPEN" | "CLASSIFIED" | "WAITING_CUSTOMER" | "RESOLVED" | "ACTION_REQUIRED" | "ENGINEER_ESCALATION";
  created_at: string;
  updated_at: string;
};

export type SupportResponse = {
  session_id: string;
  problem_details: ProblemDetails;
  customer_response: string;
  customer_facts: string[];
  citations: CustomerDocumentCitation[];
  resolution: CustomerResolution | null;
  verification_result: CustomerVerification | null;
  verification_source: "customer_confirmation" | "tool_verification" | null;
  ticket_id: number | null;
  status: "started" | "waiting_for_customer" | "waiting_for_verification" | "resolved" | "unresolved" | "needs_assistance" | "support_resolved" | "action_required" | "engineer_escalation";
};

let backendUrl = import.meta.env.VITE_BACKEND_URL ?? "http://127.0.0.1:8000";

if (backendUrl.endsWith("/")) {
  backendUrl = backendUrl.slice(0, -1);
}

export async function getBackendHealth(): Promise<BackendHealth> {
  try {
    const response = await fetch(`${backendUrl}/health/ready`);

    if (!response.ok) {
      return { isReady: false, label: "不可用" };
    }

    const data = (await response.json()) as HealthResponse;

    if (data.status === "ready") {
      return { isReady: true, label: "正常" };
    }

    return { isReady: false, label: "不可用" };
  } catch {
    return { isReady: false, label: "不可用" };
  }
}

export async function sendCustomerMessage(sessionId: string | null, message: string): Promise<SupportResponse> {
  let url = `${backendUrl}/api/v1/support-sessions`;
  let requestBody = JSON.stringify({ customer_id: "customer_001", message: message });

  if (sessionId !== null) {
    url = `${backendUrl}/api/v1/support-sessions/${encodeURIComponent(sessionId)}/messages`;
    requestBody = JSON.stringify({ message: message });
  }

  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: requestBody,
  });

  if (response.status === 404) {
    throw new Error("当前会话已不可用，请开始新的会话。");
  }

  if (response.status === 422) {
    throw new Error("请输入 1 到 5,000 个字符的消息。");
  }

  if (!response.ok) {
    throw new Error("暂时无法处理你的消息，请重试。");
  }

  return (await response.json()) as SupportResponse;
}

export async function getTicket(ticketId: number): Promise<TicketResponse> {
  const response = await fetch(`${backendUrl}/api/v1/tickets/${ticketId}`);

  if (response.status === 404) {
    throw new Error("Ticket not found.");
  }

  if (!response.ok) {
    throw new Error("The ticket could not be loaded. Please try again.");
  }

  return (await response.json()) as TicketResponse;
}
