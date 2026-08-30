import { createServer } from "node:http";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { randomUUID } from "node:crypto";

const port = Number(process.env.OPENCLAW_DELIVERY_PORT || 18790);
const secret = process.env.OPENCLAW_DELIVERY_SHARED_SECRET || "";
const configPath = process.env.OPENCLAW_CONFIG || `${process.env.HOME}/.openclaw/openclaw.json`;
const pluginRoot = process.env.OPENCLAW_WEIXIN_PLUGIN_ROOT || "";
const dedupePath =
  process.env.OPENCLAW_DELIVERY_DEDUPE_FILE ||
  `${process.env.HOME}/.openclaw/agentos-deliveries.json`;

if (!secret || !pluginRoot) {
  throw new Error("OPENCLAW_DELIVERY_SHARED_SECRET and OPENCLAW_WEIXIN_PLUGIN_ROOT are required");
}

const { resolveWeixinAccount } = await import(path.join(pluginRoot, "dist/src/auth/accounts.js"));
const {
  clearStaleAccountsForUserId,
  registerWeixinAccountId,
  saveWeixinAccount,
  triggerWeixinChannelReload,
} = await import(path.join(pluginRoot, "dist/src/auth/accounts.js"));
const { sendMessageWeixin } = await import(path.join(pluginRoot, "dist/src/messaging/send.js"));
const { DEFAULT_ILINK_BOT_TYPE, startWeixinLoginWithQr, waitForWeixinLogin } = await import(
  path.join(pluginRoot, "dist/src/auth/login-qr.js")
);
const { normalizeAccountId } = await import(
  path.join(pluginRoot, "node_modules/openclaw/dist/plugin-sdk/account-id.js")
);
const config = JSON.parse(await readFile(configPath, "utf8"));
const loginSessions = new Map();

async function readDedupe() {
  try {
    return JSON.parse(await readFile(dedupePath, "utf8"));
  } catch {
    return {};
  }
}

function json(response, status, body) {
  response.writeHead(status, { "content-type": "application/json" });
  response.end(JSON.stringify(body));
}

function authorized(request) {
  return request.headers.authorization === `Bearer ${secret}`;
}

async function startLogin() {
  const sessionId = randomUUID();
  const started = await startWeixinLoginWithQr({
    accountId: sessionId,
    apiBaseUrl: "https://ilinkai.weixin.qq.com",
    botType: DEFAULT_ILINK_BOT_TYPE,
  });
  if (!started.qrcodeUrl) throw new Error(started.message);
  const session = {
    status: "pending",
    qrcodeUrl: started.qrcodeUrl,
    expiresAt: Date.now() + 10 * 60 * 1000,
  };
  loginSessions.set(sessionId, session);
  void waitForWeixinLogin({
    sessionKey: started.sessionKey,
    apiBaseUrl: "https://ilinkai.weixin.qq.com",
    timeoutMs: 8 * 60 * 1000,
    botType: DEFAULT_ILINK_BOT_TYPE,
  })
    .then(async (result) => {
      if (!result.connected || !result.botToken || !result.accountId || !result.userId) {
        session.status = "failed";
        session.error = result.message;
        return;
      }
      const accountId = normalizeAccountId(result.accountId);
      saveWeixinAccount(accountId, {
        token: result.botToken,
        baseUrl: result.baseUrl,
        userId: result.userId,
      });
      registerWeixinAccountId(accountId);
      clearStaleAccountsForUserId(accountId, result.userId);
      await triggerWeixinChannelReload();
      session.status = "completed";
      session.accountId = accountId;
      session.peerId = result.userId;
    })
    .catch((error) => {
      session.status = "failed";
      session.error = String(error).slice(0, 300);
    });
  return {
    sessionId,
    qrcodeUrl: started.qrcodeUrl,
    expiresAt: new Date(session.expiresAt).toISOString(),
  };
}

const server = createServer(async (request, response) => {
  if (!authorized(request)) return json(response, 401, { error_code: "unauthorized" });
  if (request.method === "POST" && request.url === "/internal/agentos/weixin/login") {
    try {
      const login = await startLogin();
      return json(response, 201, {
        session_id: login.sessionId,
        qrcode_url: login.qrcodeUrl,
        expires_at: login.expiresAt,
      });
    } catch (error) {
      return json(response, 502, {
        error_code: "weixin_login_start_failed",
        detail: String(error).slice(0, 300),
      });
    }
  }
  const loginMatch = request.url?.match(/^\/internal\/agentos\/weixin\/login\/([0-9a-f-]+)$/i);
  if (request.method === "GET" && loginMatch) {
    const session = loginSessions.get(loginMatch[1]);
    if (!session || session.expiresAt <= Date.now())
      return json(response, 404, { error_code: "login_not_found" });
    return json(response, 200, {
      status: session.status,
      ...(session.status === "completed"
        ? { account_id: session.accountId, peer_id: session.peerId }
        : {}),
      ...(session.status === "failed" ? { error: session.error } : {}),
    });
  }
  if (request.method !== "POST" || request.url !== "/internal/agentos/weixin/send")
    return json(response, 404, { error_code: "not_found" });
  let body = "";
  for await (const chunk of request) body += chunk;
  let payload;
  try {
    payload = JSON.parse(body);
  } catch {
    return json(response, 400, { error_code: "invalid_json" });
  }
  if (
    payload?.channel !== "openclaw-weixin" ||
    typeof payload?.delivery_id !== "string" ||
    typeof payload?.account_id !== "string" ||
    typeof payload?.peer_id !== "string" ||
    typeof payload?.text !== "string"
  )
    return json(response, 400, { error_code: "invalid_payload" });
  const dedupe = await readDedupe();
  if (dedupe[payload.delivery_id])
    return json(response, 200, {
      accepted: true,
      duplicate: true,
      message_id: dedupe[payload.delivery_id],
    });
  try {
    const account = resolveWeixinAccount(config, payload.account_id);
    if (!account.configured || !account.token)
      return json(response, 409, { error_code: "account_not_configured" });
    const result = await sendMessageWeixin({
      to: payload.peer_id,
      text: payload.text,
      opts: { baseUrl: account.baseUrl, token: account.token, runId: payload.delivery_id },
    });
    dedupe[payload.delivery_id] = result.messageId;
    const entries = Object.entries(dedupe).slice(-2000);
    await writeFile(dedupePath, JSON.stringify(Object.fromEntries(entries)), { mode: 0o600 });
    return json(response, 200, { accepted: true, message_id: result.messageId });
  } catch (error) {
    return json(response, 502, {
      error_code: "weixin_send_failed",
      retryable: true,
      detail: String(error).slice(0, 300),
    });
  }
});

server.listen(port, "127.0.0.1", () =>
  console.log(`AgentOS Weixin delivery adapter listening on 127.0.0.1:${port}`),
);
