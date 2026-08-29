import { createServer } from "node:http";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";

const port = Number(process.env.OPENCLAW_DELIVERY_PORT || 18790);
const secret = process.env.OPENCLAW_DELIVERY_SHARED_SECRET || "";
const accountId = process.env.OPENCLAW_WEIXIN_ACCOUNT_ID || "";
const configPath = process.env.OPENCLAW_CONFIG || `${process.env.HOME}/.openclaw/openclaw.json`;
const pluginRoot = process.env.OPENCLAW_WEIXIN_PLUGIN_ROOT || "";
const dedupePath =
  process.env.OPENCLAW_DELIVERY_DEDUPE_FILE ||
  `${process.env.HOME}/.openclaw/agentos-deliveries.json`;

if (!secret || !accountId || !pluginRoot) {
  throw new Error(
    "OPENCLAW_DELIVERY_SHARED_SECRET, OPENCLAW_WEIXIN_ACCOUNT_ID and OPENCLAW_WEIXIN_PLUGIN_ROOT are required",
  );
}

const { resolveWeixinAccount } = await import(path.join(pluginRoot, "dist/src/auth/accounts.js"));
const { sendMessageWeixin } = await import(path.join(pluginRoot, "dist/src/messaging/send.js"));
const config = JSON.parse(await readFile(configPath, "utf8"));
const account = resolveWeixinAccount(config, accountId);
if (!account.configured || !account.token)
  throw new Error(`Weixin account is not configured: ${accountId}`);

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

const server = createServer(async (request, response) => {
  if (request.method !== "POST" || request.url !== "/internal/agentos/weixin/send")
    return json(response, 404, { error_code: "not_found" });
  if (request.headers.authorization !== `Bearer ${secret}`)
    return json(response, 401, { error_code: "unauthorized" });
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
