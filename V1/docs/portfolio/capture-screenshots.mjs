import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const projectRoot = path.resolve(import.meta.dirname, "../..");
const screenshotDirectory = path.join(import.meta.dirname, "screenshots");
const edgePath = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const debugPort = 9223;
const browserProfile = fs.mkdtempSync(path.join(os.tmpdir(), "resolveai-screenshots-"));
const screenshots = [
  { reportFile: "debug_agent_001.json", caseId: "agent_001", fileName: "customer-resolution.png", supportView: false },
  { reportFile: "debug_agent_007_final.json", caseId: "agent_007", fileName: "support-resolution.png", supportView: true },
  { reportFile: "debug_agent_013_retry.json", caseId: "agent_013", fileName: "approval-waiting.png", supportView: true },
  { reportFile: "debug_agent_007_diagnostic.json", caseId: "agent_007", fileName: "engineer-escalation.png", supportView: true },
];

function wait(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function waitForBrowser() {
  for (let attempt = 0; attempt < 30; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${debugPort}/json/version`);

      if (response.ok) {
        return;
      }
    } catch {
      await wait(250);
    }
  }

  throw new Error("Headless browser did not start");
}

async function openTarget(url) {
  const response = await fetch(`http://127.0.0.1:${debugPort}/json/new?${encodeURIComponent(url)}`, { method: "PUT" });

  if (!response.ok) {
    throw new Error(`Could not open browser target: ${response.status}`);
  }

  return response.json();
}

async function connectToTarget(target) {
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  const pending = new Map();
  let nextId = 1;

  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });

  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    const current = pending.get(message.id);

    if (current === undefined) {
      return;
    }

    pending.delete(message.id);

    if (message.error !== undefined) {
      current.reject(new Error(message.error.message));
    } else {
      current.resolve(message.result);
    }
  });

  function send(method, params = {}) {
    const id = nextId;
    nextId += 1;

    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      socket.send(JSON.stringify({ id, method, params }));
    });
  }

  return { send, close: () => socket.close() };
}

async function captureScreenshot(definition) {
  const reportPath = path.join(projectRoot, "evals/reports", definition.reportFile);
  const report = JSON.parse(fs.readFileSync(reportPath, "utf8"));
  const result = report.results.find((currentResult) => currentResult.case_id === definition.caseId);
  const sessionId = result?.actual?.session_id;
  const customerId = result?.actual?.expected_customer_id;

  if (typeof sessionId !== "string" || typeof customerId !== "string") {
    throw new Error(`Evaluation result ${definition.caseId} has no saved session`);
  }

  const url = `http://127.0.0.1:3000/?customer=${encodeURIComponent(customerId)}`;
  const target = await openTarget(url);
  const connection = await connectToTarget(target);

  try {
    await connection.send("Page.enable");
    await connection.send("Runtime.enable");
    await connection.send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
    await wait(2500);
    const storageKey = `resolveai_support_session_id:${customerId}`;
    await connection.send("Runtime.evaluate", { expression: `localStorage.setItem(${JSON.stringify(storageKey)}, ${JSON.stringify(sessionId)}); location.reload();` });
    await wait(4000);

    if (definition.supportView) {
      await connection.send("Runtime.evaluate", { expression: `Array.from(document.querySelectorAll("button")).find((button) => button.textContent?.includes("支持视图"))?.click()` });
      await wait(3500);
    }

    const screenshot = await connection.send("Page.captureScreenshot", { format: "png", fromSurface: true, captureBeyondViewport: true });
    fs.writeFileSync(path.join(screenshotDirectory, definition.fileName), Buffer.from(screenshot.data, "base64"));
  } finally {
    connection.close();
    await fetch(`http://127.0.0.1:${debugPort}/json/close/${target.id}`);
  }
}

if (!fs.existsSync(edgePath)) {
  throw new Error("Microsoft Edge was not found at the expected path");
}

fs.mkdirSync(screenshotDirectory, { recursive: true });
const browser = spawn(edgePath, ["--headless=new", "--disable-gpu", "--no-first-run", `--remote-debugging-port=${debugPort}`, `--user-data-dir=${browserProfile}`, "about:blank"], { stdio: "ignore" });

try {
  await waitForBrowser();

  for (const definition of screenshots) {
    await captureScreenshot(definition);
  }
} finally {
  browser.kill();

  if (browser.exitCode === null) {
    await new Promise((resolve) => browser.once("exit", resolve));
  }

  if (browserProfile.startsWith(os.tmpdir())) {
    fs.rmSync(browserProfile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  }
}
