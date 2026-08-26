import "server-only";

export type BackendHealth = {
  isReady: boolean;
  label: "Ready" | "Unavailable";
};

export async function getBackendHealth(): Promise<BackendHealth> {
  const backendUrl = (
    process.env.BACKEND_URL ?? "http://127.0.0.1:8000"
  ).replace(/\/$/, "");

  try {
    const response = await fetch(`${backendUrl}/health/ready`, {
      cache: "no-store",
      signal: AbortSignal.timeout(3000),
    });

    if (!response.ok) {
      return { isReady: false, label: "Unavailable" };
    }

    const data: unknown = await response.json();

    if (
      typeof data === "object" &&
      data !== null &&
      "status" in data &&
      data.status === "ready"
    ) {
      return { isReady: true, label: "Ready" };
    }

    return { isReady: false, label: "Unavailable" };
  } catch {
    return { isReady: false, label: "Unavailable" };
  }
}
