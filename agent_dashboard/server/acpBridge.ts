/**
 * ACP ⇄ WebSocket bridge.
 *
 * Browsers cannot speak ACP: the protocol is JSON-RPC over the agent's
 * stdin/stdout. This bridge spawns an ACP agent as a child process and pumps
 * newline-delimited JSON-RPC messages between its stdio and a WebSocket text
 * frame, one message per frame — the framing that
 * `@agentclientprotocol/sdk/experimental/ws-client` expects.
 *
 * Usage:
 *   tsx server/acpBridge.ts                # default 127.0.0.1:8787
 *   ACP_BRIDGE_PORT=9000 tsx server/acpBridge.ts
 *
 * Endpoints:
 *   GET  /health                → { agents: [...], graceMs }
 *   WS   /acp?agent=codex&cwd=/abs/path&session=<key>
 *
 * `session` is optional but required to survive a browser reload: with a
 * stable key, the child process is kept alive for ACP_BRIDGE_GRACE_MS after a
 * disconnect and the next connection reattaches to it. Without it the child is
 * killed as soon as the socket closes.
 */
import { spawn, type ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";
import { createServer, type IncomingMessage } from "node:http";
import { homedir } from "node:os";
import { delimiter, join } from "node:path";
import { WebSocketServer, type WebSocket } from "ws";

const PORT = Number(process.env.ACP_BRIDGE_PORT || 8787);
const HOST = process.env.ACP_BRIDGE_HOST || "127.0.0.1";
const GRACE_MS = Number(process.env.ACP_BRIDGE_GRACE_MS || 0);

type AgentSpec = {
  command: string;
  args: string[];
  /**
   * CLI the adapter drives. Checked before spawning so a missing dependency
   * produces install instructions instead of a bare `spawn ENOENT`.
   */
  requires?: { command: string; install: string };
};

/**
 * Agents the bridge knows how to launch.
 *
 * `opencode acp` is the native choice: no download, no separate adapter, it
 * ships its own ACP server. The others are the official Zed adapters, which
 * download on first use via npx but still need the vendor CLI installed.
 */
const AGENTS: Record<string, AgentSpec> = {
  opencode: {
    command: "opencode",
    args: ["acp"],
    requires: { command: "opencode", install: "npm install -g opencode-ai" },
  },
  claude: {
    command: "npx",
    args: ["--yes", "@agentclientprotocol/claude-agent-acp@latest"],
    requires: { command: "claude", install: "npm install -g @anthropic-ai/claude-code" },
  },
  codex: {
    command: "npx",
    args: ["--yes", "@zed-industries/codex-acp@latest"],
    requires: { command: "codex", install: "npm install -g @openai/codex" },
  },
};

/** Directories searched on top of PATH, for CLIs that install outside it. */
const EXTRA_BIN_DIRS = [
  join(homedir(), ".opencode", "bin"),
  join(homedir(), ".local", "bin"),
  join(homedir(), ".bun", "bin"),
];

function searchDirs(): string[] {
  const override = (process.env.ACP_BRIDGE_PATH ?? "").split(delimiter).filter(Boolean);
  const fromPath = (process.env.PATH ?? "").split(delimiter).filter(Boolean);
  return [...override, ...fromPath, ...EXTRA_BIN_DIRS];
}

/** Absolute path of `command`, or null when it is not installed. */
function resolveExecutable(command: string): string | null {
  if (command.includes("/")) return existsSync(command) ? command : null;
  for (const dir of searchDirs()) {
    const candidate = join(dir, command);
    if (existsSync(candidate)) return candidate;
  }
  return null;
}

type AgentStatus = {
  name: string;
  available: boolean;
  missing: string | null;
  install: string | null;
};

function agentStatus(name: string, spec: AgentSpec): AgentStatus {
  const required = spec.requires;
  if (!required || resolveExecutable(required.command)) {
    return { name, available: true, missing: null, install: null };
  }
  return {
    name,
    available: false,
    missing: required.command,
    install: required.install,
  };
}

type BridgeSession = {
  key: string;
  child: ChildProcess;
  socket: WebSocket | null;
  reapTimer: NodeJS.Timeout | null;
  spec: AgentSpec;
  cwd: string;
  /** Set once a client frame reached the agent; guards the spawn-error path. */
  forwarded: boolean;
  /** id of the client's first request, so a failed spawn can answer it. */
  firstRequestId: unknown;
};

const sessions = new Map<string, BridgeSession>();

function log(...args: unknown[]): void {
  console.log(`[acp-bridge ${new Date().toISOString()}]`, ...args);
}

function writeFrame(socket: WebSocket, line: string): void {
  if (socket.readyState !== socket.OPEN) return;
  socket.send(line);
}

function attach(session: BridgeSession, socket: WebSocket): void {
  session.socket = socket;
  if (session.reapTimer) {
    clearTimeout(session.reapTimer);
    session.reapTimer = null;
  }

  const child = session.child;
  let outBuf = "";

  const onStdout = (chunk: Buffer): void => {
    outBuf += chunk.toString("utf8");
    let index = outBuf.indexOf("\n");
    while (index !== -1) {
      const line = outBuf.slice(0, index).trim();
      outBuf = outBuf.slice(index + 1);
      if (line) writeFrame(socket, line);
      index = outBuf.indexOf("\n");
    }
  };

  const onStderr = (chunk: Buffer): void => {
    const text = chunk.toString("utf8").trimEnd();
    if (text) log(`[${session.key}] agent stderr: ${text.slice(0, 2000)}`);
  };

  const onExit = (code: number | null, signal: NodeJS.Signals | null): void => {
    log(`[${session.key}] agent exited code=${code} signal=${signal}`);
    // Let the client see why the connection went away instead of hanging.
    const notice = JSON.stringify({
      jsonrpc: "2.0",
      method: "session/update",
      params: {
        sessionId: "",
        update: {
          sessionUpdate: "notice",
          message: `ACP agent exited (code=${code ?? "null"})`,
        },
      },
    });
    writeFrame(socket, notice);
    cleanup(session);
    if (socket.readyState === socket.OPEN) socket.close(1011, "agent exited");
  };

  child.stdout?.on("data", onStdout);
  child.stderr?.on("data", onStderr);
  child.on("exit", onExit);

  socket.on("message", (data) => {
    const raw = typeof data === "string" ? data : data.toString("utf8");
    for (const line of raw.split("\n")) {
      if (!line.trim()) continue;
      if (!session.forwarded) {
        session.forwarded = true;
        // Keep the request id: if the child turns out to be unspawnable, the
        // client still gets an error response instead of a dangling request.
        try {
          session.firstRequestId = JSON.parse(line).id;
        } catch {
          session.firstRequestId = null;
        }
      }
      if (child.stdin && !child.stdin.destroyed) child.stdin.write(`${line}\n`);
    }
  });

  socket.on("close", () => {
    log(`[${session.key}] client disconnected`);
    child.stdout?.off("data", onStdout);
    child.stderr?.off("data", onStderr);
    child.off("exit", onExit);
    session.socket = null;
    if (!GRACE_MS || session.key.startsWith("ephemeral:")) {
      cleanup(session);
      child.kill("SIGTERM");
      setTimeout(() => {
        if (!child.killed) child.kill("SIGKILL");
      }, 3000).unref?.();
      return;
    }
    session.reapTimer = setTimeout(() => {
      log(`[${session.key}] grace period expired, stopping agent`);
      cleanup(session);
      child.kill("SIGTERM");
    }, GRACE_MS);
    session.reapTimer.unref?.();
  });

  socket.on("error", (error) => log(`[${session.key}] socket error: ${String(error)}`));
  log(`[${session.key}] attached (pid=${child.pid})`);
}

function cleanup(session: BridgeSession): void {
  if (session.reapTimer) clearTimeout(session.reapTimer);
  sessions.delete(session.key);
}

function resolveSpec(params: URLSearchParams): { name: string; spec: AgentSpec } | null {
  const override = params.get("command");
  if (override) {
    const parts = override.split(" ").filter(Boolean);
    if (parts.length) {
      return { name: parts[0], spec: { command: parts[0], args: parts.slice(1) } };
    }
  }
  const name = params.get("agent") || "opencode";
  const spec = AGENTS[name];
  return spec ? { name, spec } : null;
}

/**
 * Refuses a connection with a readable reason.
 *
 * A WebSocket close reason is capped at 123 bytes, far too short for install
 * instructions, so the reason is delivered as a JSON-RPC error response to the
 * client's request — which is what surfaces in the browser UI. When `id` is
 * unknown the next incoming frame is answered instead.
 */
function failSession(socket: WebSocket, key: string, message: string, id?: unknown): void {
  log(`[${key}] rejected: ${message}`);
  const answer = (requestId: unknown): void => {
    if (socket.readyState === socket.OPEN) {
      socket.send(
        JSON.stringify({ jsonrpc: "2.0", id: requestId ?? null, error: { code: -32001, message } }),
      );
    }
    const closing = setTimeout(() => socket.close(1011, "rejected"), 1500);
    closing.unref?.();
  };
  if (id !== undefined) {
    answer(id);
    return;
  }
  const giveUp = setTimeout(() => socket.close(1011, "rejected"), 5000);
  socket.once("message", (data) => {
    clearTimeout(giveUp);
    let requestId: unknown = null;
    try {
      requestId = JSON.parse(String(data).split("\n")[0]).id ?? null;
    } catch {
      // Unparseable frame: answer without an id.
    }
    answer(requestId);
  });
}

function handleConnection(socket: WebSocket, request: IncomingMessage): void {
  const url = new URL(request.url || "/", `http://${request.headers.host || "localhost"}`);
  const params = url.searchParams;
  const cwd = params.get("cwd") || process.cwd();
  const rawKey = params.get("session");
  const key = rawKey || `ephemeral:${Date.now()}:${Math.random().toString(36).slice(2, 8)}`;

  const resolved = resolveSpec(params);
  if (!resolved) {
    failSession(
      socket,
      key,
      `unknown agent "${params.get("agent") ?? ""}"; available: ${Object.keys(AGENTS).join(", ")}`,
    );
    return;
  }
  const { name, spec } = resolved;

  const existing = sessions.get(key);
  if (existing && !existing.socket) {
    attach(existing, socket);
    return;
  }
  if (existing) {
    socket.close(1013, "session key already in use");
    return;
  }

  const env: NodeJS.ProcessEnv = { ...process.env };
  params.forEach((value, name) => {
    if (name.startsWith("env.") && value) env[name.slice(4)] = value;
  });

  // The browser cannot stat the bridge host, so a typo here is common. Fail
  // with a readable reason instead of letting `spawn` report a bare ENOENT.
  if (!existsSync(cwd)) {
    failSession(socket, key, `cwd does not exist on the bridge host: ${cwd}`);
    return;
  }

  // Probe the vendor CLI before spawning so a missing dependency turns into
  // install instructions rather than a bare `spawn ENOENT`.
  const status = agentStatus(name, spec);
  if (!status.available) {
    failSession(
      socket,
      key,
      `${status.missing} is not installed on the bridge host. Install it with: ${status.install}`,
    );
    return;
  }

  const executable = resolveExecutable(spec.command) ?? spec.command;
  log(`[${key}] spawning ${executable} ${spec.args.join(" ")} (cwd=${cwd})`);
  let child: ChildProcess;
  try {
    child = spawn(executable, spec.args, { cwd, env, stdio: ["pipe", "pipe", "pipe"] });
  } catch (error) {
    failSession(socket, key, `cannot spawn agent: ${String(error)}`);
    return;
  }

  const session: BridgeSession = {
    key,
    child,
    socket: null,
    reapTimer: null,
    spec,
    cwd,
    forwarded: false,
    firstRequestId: null,
  };
  sessions.set(key, session);

  // `spawn` reports ENOENT/EACCES asynchronously. Without this listener the
  // error is unhandled and takes down the whole bridge process.
  child.on("error", (error) => {
    log(`[${key}] spawn failed: ${String(error)}`);
    cleanup(session);
    // Answer the client's in-flight request so the UI shows the reason
    // instead of waiting for its own timeout.
    failSession(
      socket,
      key,
      `cannot spawn agent: ${String(error)}`,
      session.forwarded ? session.firstRequestId : undefined,
    );
  });

  attach(session, socket);
}

const server = createServer((req, res) => {
  if (req.url?.startsWith("/health")) {
    // 前端跑在 Vite 源（localhost:3000），桥接在 127.0.0.1:8787 —— 跨源，
    // 不放开 CORS 浏览器会直接丢弃响应，探测结果就永远拿不到。
    res.writeHead(200, {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, OPTIONS",
      "Cache-Control": "no-store",
    });
    res.end(
      JSON.stringify({
        agents: Object.entries(AGENTS).map(([name, spec]) => agentStatus(name, spec)),
        graceMs: GRACE_MS,
        active: Array.from(sessions.keys()),
      }),
    );
    return;
  }
  res.writeHead(404, { "Content-Type": "text/plain" });
  res.end("acp-bridge: use ws://<host>/acp?agent=<name>&cwd=<path>");
});

const wss = new WebSocketServer({ noServer: true });
server.on("upgrade", (req, socket, head) => {
  if (!req.url?.startsWith("/acp")) {
    socket.destroy();
    return;
  }
  wss.handleUpgrade(req, socket, head, (ws) => handleConnection(ws, req));
});

server.listen(PORT, HOST, () => {
  log(`listening on ws://${HOST}:${PORT}/acp (grace=${GRACE_MS}ms)`);
  for (const [name, spec] of Object.entries(AGENTS)) {
    const status = agentStatus(name, spec);
    log(
      status.available
        ? `agent ${name}: ready`
        : `agent ${name}: MISSING ${status.missing} — install with: ${status.install}`,
    );
  }
});
