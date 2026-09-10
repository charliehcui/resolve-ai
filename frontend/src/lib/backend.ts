export type BackendHealth = {
  isReady: boolean;
  label: "Ready" | "Unavailable";
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

export type SupportResponse = {
  session_id: string;
  problem_details: ProblemDetails;
  customer_response: string;
  customer_facts: string[];
  citations: CustomerDocumentCitation[];
  resolution: CustomerResolution | null;
  verification_result: CustomerVerification | null;
  verification_source: "customer_confirmation" | "tool_verification" | null;
  status: "started" | "waiting_for_customer" | "waiting_for_verification" | "resolved" | "unresolved" | "needs_assistance";
};

let backendUrl = import.meta.env.VITE_BACKEND_URL ?? "http://127.0.0.1:8000";

if (backendUrl.endsWith("/")) {
  backendUrl = backendUrl.slice(0, -1);
}

export async function getBackendHealth(): Promise<BackendHealth> {
  try {
    const response = await fetch(`${backendUrl}/health/ready`);

    if (!response.ok) {
      return { isReady: false, label: "Unavailable" };
    }

    const data = (await response.json()) as HealthResponse;

    if (data.status === "ready") {
      return { isReady: true, label: "Ready" };
    }

    return { isReady: false, label: "Unavailable" };
  } catch {
    return { isReady: false, label: "Unavailable" };
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
    throw new Error("This conversation is no longer available. Please start a new conversation.");
  }

  if (response.status === 422) {
    throw new Error("Please enter a message between 1 and 5,000 characters.");
  }

  if (!response.ok) {
    throw new Error("We could not process your message. Please try again.");
  }

  return (await response.json()) as SupportResponse;
}
