import { useEffect, useState } from "react";

import { getBackendHealth } from "./lib/backend";

function App() {
  const [status, setStatus] = useState("Checking...");
  const [isReady, setIsReady] = useState(false);

  useEffect(() => {
    async function checkBackend() {
      const backendHealth = await getBackendHealth();
      setStatus(backendHealth.label);
      setIsReady(backendHealth.isReady);
    }

    checkBackend();
  }, []);

  let statusColor = "bg-amber-400";

  if (status === "Unavailable") {
    statusColor = "bg-red-500";
  }

  if (isReady) {
    statusColor = "bg-emerald-500";
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-950 px-6 text-slate-100">
      <section className="w-full max-w-lg rounded-2xl border border-slate-800 bg-slate-900 p-8 shadow-xl">
        <p className="text-sm font-semibold uppercase tracking-[0.25em] text-cyan-400">ResolveAI</p>
        <h1 className="mt-3 text-3xl font-semibold">Agent support workspace</h1>
        <p className="mt-3 leading-7 text-slate-400">
          This lightweight interface will host the customer conversation and support investigation views. It currently verifies that the API and database can be reached.
        </p>

        <div className="mt-8 flex items-center justify-between rounded-xl border border-slate-700 bg-slate-950 px-5 py-4">
          <div>
            <p className="text-sm text-slate-400">Backend status</p>
            <p className="mt-1 font-medium">{status}</p>
          </div>
          <span className={`h-3 w-3 rounded-full ${statusColor}`} aria-label={`Backend ${status}`} />
        </div>
      </section>
    </main>
  );
}

export default App;
