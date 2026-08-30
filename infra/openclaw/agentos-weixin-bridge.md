# AgentOS Weixin bridge deployment

AgentOS owns the notification outbox and user-facing QR login. OpenClaw exposes
loopback-only `POST /internal/agentos/weixin/login`,
`GET /internal/agentos/weixin/login/{session_id}`, and
`POST /internal/agentos/weixin/send` endpoints that validate
`OPENCLAW_DELIVERY_SHARED_SECRET`, requires `channel=openclaw-weixin`, and calls
the plugin's `sendText({ accountId, to: peer_id, text })` directly. QR login creates
one OpenClaw account for the scanned Weixin user. The adapter persists the credential;
AgentOS records only the resulting `account_id + peer_id` against the authenticated user.
It must
return `{ accepted: true, message_id }` and deduplicate `delivery_id` for at
least 24 hours. It must never call `chat.send`, an agent, or a model.

The installed Tencent Weixin plugin currently has no versioned source checkout
in this repository. Before enabling `SCHEDULED_TASK_WEIXIN_ENABLED`, install a
versioned plugin build containing these two changes in `processOneMessage`:

1. After `handleBindingBridgeMessage`, send `bindingResult.reply` and return
   for every callback response, including ordinary text. On callback timeout or
   non-2xx, send the fixed rejection text and return. Do not enter slash-command
   or `dispatchReplyFromConfig` handling.
2. Set `replyProgressMessages=false` and ensure the channel has no default
   model/session route for inbound messages.

The AgentOS worker endpoint and the binding callback are separate secrets. Both
must remain loopback-only and must not be published through FRP, Nginx, or a
browser-facing API.

## Install the adapter

Copy `com.local.openclaw-agentos-delivery.plist.example` to
`~/Library/LaunchAgents/com.local.openclaw-agentos-delivery.plist`, replacing
`__AGENTOS_ROOT__`, `__OPENCLAW_HOME__`, `__OPENCLAW_PLUGIN_ROOT__`,
`__NODE_BIN__`, and `__DELIVERY_SECRET__`. The current installation values are:

```text
__NODE_BIN__=/Users/randyhsu/.nvm/versions/node/v24.19.0/bin/node
__OPENCLAW_PLUGIN_ROOT__=/Users/randyhsu/.openclaw/npm/projects/tencent-weixin-openclaw-weixin-7783ac86ba/node_modules/@tencent-weixin/openclaw-weixin
```

Then load it with `launchctl bootstrap gui/$(id -u) ...plist`. Verify
`curl -i http://127.0.0.1:18790/health` is intentionally not supported; a
`404` on `GET /` is expected, while an authenticated POST from AgentOS is the
functional check. Restart AgentOS after the adapter is listening.
