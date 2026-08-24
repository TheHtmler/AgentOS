# AgentOS Sandbox Manager

This is a private execution boundary for `sandbox_exec`. Agent API talks to it over
an internal HTTP endpoint authenticated by `X-AgentOS-Sandbox-Token`.

Each user gets a separate workspace directory. Every command runs in a short-lived
Docker container with:

- no network (`--network none`);
- non-root host UID/GID;
- read-only container root filesystem;
- a read-write mount only at `/workspace`;
- dropped Linux capabilities and `no-new-privileges`;
- bounded memory, CPU, PIDs, output, and wall-clock time.

The manager must not be exposed through Nginx/FRP. The Agent API should be the only
caller. The configured image must include `/bin/sh`.

Run locally with:

```bash
uv sync
SANDBOX_MANAGER_TOKEN=dev-token uv run uvicorn sandbox_manager.main:app --host 127.0.0.1 --port 8788
```
