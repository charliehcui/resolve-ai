import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  reporter: "line",
  use: { baseURL: "http://127.0.0.1:5173", headless: true },
  webServer: [
    {
      command: ".\\.venv\\Scripts\\python -m uvicorn backend.app.api:app --host 127.0.0.1 --port 8000",
      cwd: "..",
      url: "http://127.0.0.1:8000/api/v1/health",
      env: { LANGSMITH_TRACING: "false" },
      reuseExistingServer: false,
      timeout: 30000,
    },
    {
      command: "npm run dev -- --host 127.0.0.1",
      url: "http://127.0.0.1:5173",
      reuseExistingServer: false,
      timeout: 30000,
    },
  ],
});
