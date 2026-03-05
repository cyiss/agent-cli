/**
 * Railway entrypoint — health check + reverse proxy to OpenClaw gateway.
 *
 * Flow:
 *   1. Run bootstrap (auto-configure OpenClaw + MCP + Telegram)
 *   2. Start OpenClaw gateway as child process
 *   3. Serve health checks + proxy all other traffic to gateway
 */
const express = require("express");
const httpProxy = require("http-proxy");
const { bootstrap } = require("./bootstrap.mjs");
const { startGateway, waitForGatewayReady, getGatewayProcess } = require("./gateway");
const { autoOnboard } = require("./onboard");

const app = express();
const PORT = parseInt(process.env.PORT || "8080", 10);
const GATEWAY_HOST = process.env.INTERNAL_GATEWAY_HOST || "127.0.0.1";
const GATEWAY_PORT = parseInt(process.env.INTERNAL_GATEWAY_PORT || "18789", 10);
const START_TIME = Date.now();

// Proxy to OpenClaw gateway
const proxy = httpProxy.createProxyServer({
  target: `http://${GATEWAY_HOST}:${GATEWAY_PORT}`,
  ws: true,
  changeOrigin: true,
});

proxy.on("error", (err, req, res) => {
  if (res.writeHead) {
    res.writeHead(502, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: "gateway_unavailable", message: err.message }));
  }
});

// Health check
app.get("/health", (req, res) => {
  const gw = getGatewayProcess();
  res.json({
    status: "ok",
    uptime_s: Math.floor((Date.now() - START_TIME) / 1000),
    gateway_alive: gw ? !gw.killed : false,
    gateway_pid: gw ? gw.pid : null,
  });
});

// Trading status (calls hl CLI directly)
app.get("/status", async (req, res) => {
  const { execSync } = require("child_process");
  try {
    const output = execSync("python3 -m cli.main wolf status", {
      timeout: 10000,
      encoding: "utf-8",
      cwd: "/agent-cli",
    });
    res.type("text/plain").send(output);
  } catch (e) {
    res.type("text/plain").send(e.stdout || e.stderr || e.message);
  }
});

// Everything else proxies to OpenClaw gateway
app.use((req, res) => {
  proxy.web(req, res);
});

// WebSocket upgrade
const server = app.listen(PORT, async () => {
  console.log(`[server] Listening on :${PORT}`);

  try {
    // Step 1: Bootstrap (create dirs, sync workspace, generate configs)
    await bootstrap();

    // Step 2: Auto-onboard if credentials present
    await autoOnboard();

    // Step 3: Start OpenClaw gateway
    startGateway();
    await waitForGatewayReady();
    console.log("[server] OpenClaw gateway is ready");
  } catch (err) {
    console.error("[server] Startup error:", err.message);
    // Keep server running for health checks even if gateway fails
  }
});

server.on("upgrade", (req, socket, head) => {
  proxy.ws(req, socket, head);
});

// Graceful shutdown
function shutdown(signal) {
  console.log(`[server] ${signal} received, shutting down`);
  const gw = getGatewayProcess();
  if (gw && !gw.killed) {
    gw.kill("SIGTERM");
    setTimeout(() => {
      if (!gw.killed) gw.kill("SIGKILL");
    }, 10000);
  }
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(1), 15000);
}

process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));
