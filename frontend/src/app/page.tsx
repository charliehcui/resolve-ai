import { getBackendHealth } from "@/lib/backend";

export const dynamic = "force-dynamic";

export default async function Home() {
  const backendHealth = await getBackendHealth();

  return (
    <main className="flex min-h-screen items-center justify-center px-6 py-16">
      <section className="w-full max-w-2xl rounded-2xl border border-slate-200 bg-white p-8 shadow-sm">
        <p className="text-sm font-semibold uppercase tracking-[0.2em] text-blue-700">
          ResolveAI
        </p>

        <h1 className="mt-3 text-3xl font-semibold tracking-tight text-slate-950">
          Technical Support Console
        </h1>

        <p className="mt-3 max-w-xl leading-7 text-slate-600">
          The product scaffold is running. This page checks whether the FastAPI
          backend is ready to receive requests.
        </p>

        <div className="mt-8 rounded-xl border border-slate-200 bg-slate-50 p-5">
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="font-medium text-slate-900">Backend API</p>
              <p className="mt-1 font-mono text-sm text-slate-500">
                GET /health/ready
              </p>
            </div>

            <div
              className={`inline-flex items-center gap-2 rounded-full px-3 py-1.5 text-sm font-medium ${
                backendHealth.isReady
                  ? "bg-emerald-100 text-emerald-800"
                  : "bg-red-100 text-red-800"
              }`}
            >
              <span
                className={`size-2 rounded-full ${
                  backendHealth.isReady ? "bg-emerald-500" : "bg-red-500"
                }`}
              />
              {backendHealth.label}
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}
