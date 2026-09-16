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

export type CustomerConversationMessage = {
  role: "customer" | "assistant";
  content: string;
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

export type EvidenceItem = {
  evidence_id: string;
  ticket_id: number;
  customer_id: string;
  source_type: "tool" | "document";
  source_reference: string;
  observed_at: string;
  summary: string;
  customer_visibility: "INTERNAL";
  feature: string;
  facts: Record<string, string | number | boolean>;
};

export type ActionProposal = {
  action_name: string;
  reason: string;
  supporting_evidence_ids: string[];
  intended_target_reference: string;
  expected_result: string;
  verification_method: "read_background_operation";
};

export type ActionProposalResponse = {
  id: number;
  ticket_id: number;
  proposal: ActionProposal;
  status: string;
  policy_reason: string;
  created_at: string;
  updated_at: string;
};

export type ApprovalResponse = {
  id: number;
  proposal_id: number;
  decision: "approve" | "reject";
  reviewer_role: "demo_approver";
  created_at: string;
};

export type ActionExecutionResponse = {
  id: number;
  proposal_id: number;
  idempotency_key: string;
  status: string;
  request: Record<string, unknown>;
  before_state: Record<string, unknown> | null;
  after_state: Record<string, unknown> | null;
  external_reference: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
};

export type EngineerEscalationPackage = {
  ticket_id: number;
  customer_id: string | null;
  issue_summary: string;
  customer_impact: string;
  customer_diagnosis: SupportHandoff | null;
  internal_evidence_ids: string[];
  tools_used: string[];
  excluded_causes: string[];
  possible_causes: string[];
  unanswered_questions: string[];
  escalation_reason: string;
  next_checks: string[];
};

export type SupportInvestigationResult = {
  conclusion: string;
  root_cause: string | null;
  supporting_evidence_ids: string[];
  contradicting_evidence_ids: string[];
  confidence_band: "low" | "medium" | "high";
  resolution: string | null;
  escalation_reason: string | null;
  action_proposal: ActionProposal | null;
  supporting_facts: string[];
  internal_citation_ids: string[];
  evidence: EvidenceItem[];
  validation_errors: string[];
  escalation_package: EngineerEscalationPackage | null;
  customer_explanation: string;
  outcome: "resolution" | "action_required" | "engineer_escalation";
};

export type TicketResponse = {
  id: number;
  support_session_id: string | null;
  handoff: SupportHandoff | null;
  investigation_result: SupportInvestigationResult | null;
  investigation_tools: string[] | null;
  action_proposal: ActionProposalResponse | null;
  approval: ApprovalResponse | null;
  action_execution: ActionExecutionResponse | null;
  status: "OPEN" | "CLASSIFIED" | "WAITING_CUSTOMER" | "RESOLVED" | "ACTION_REQUIRED" | "AWAITING_APPROVAL" | "ENGINEER_ESCALATION";
  created_at: string;
  updated_at: string;
};

export type SupportResponse = {
  session_id: string;
  messages: CustomerConversationMessage[];
  problem_details: ProblemDetails;
  customer_response: string;
  customer_facts: string[];
  citations: CustomerDocumentCitation[];
  resolution: CustomerResolution | null;
  verification_result: CustomerVerification | null;
  verification_source: "customer_confirmation" | "tool_verification" | null;
  ticket_id: number | null;
  status: "started" | "waiting_for_customer" | "waiting_for_verification" | "resolved" | "unresolved" | "needs_assistance" | "support_resolved" | "action_required" | "waiting_for_approval" | "engineer_escalation";
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

export async function sendCustomerMessage(sessionId: string | null, message: string, customerId: string): Promise<SupportResponse> {
  let url = `${backendUrl}/api/v1/support-sessions`;
  let requestBody = JSON.stringify({ customer_id: customerId, message: message });

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

export async function getSupportSession(sessionId: string): Promise<SupportResponse> {
  const response = await fetch(`${backendUrl}/api/v1/support-sessions/${encodeURIComponent(sessionId)}`);

  if (response.status === 404) {
    throw new Error("当前会话已不可用，请开始新的会话。");
  }

  if (!response.ok) {
    throw new Error("暂时无法恢复当前会话，请刷新页面重试。");
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

export async function submitApproval(proposalId: number, decision: "approve" | "reject"): Promise<void> {
  const response = await fetch(`${backendUrl}/api/v1/action-proposals/${proposalId}/approval`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision: decision, reviewer_role: "demo_approver" }),
  });

  if (response.status === 404) {
    throw new Error("Action proposal not found.");
  }

  if (response.status === 409) {
    throw new Error("The action proposal cannot be resumed in its current state.");
  }

  if (!response.ok) {
    throw new Error("The approval decision could not be completed. Please try again.");
  }
}
