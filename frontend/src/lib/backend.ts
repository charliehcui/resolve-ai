export type BackendHealth = {
  isReady: boolean;
  label: "Ready" | "Unavailable";
};

type HealthResponse = {
  status?: string;
};

export async function getBackendHealth(): Promise<BackendHealth> {
  let backendUrl = import.meta.env.VITE_BACKEND_URL ?? "http://127.0.0.1:8000";

  if (backendUrl.endsWith("/")) {
    backendUrl = backendUrl.slice(0, -1);
  }

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
