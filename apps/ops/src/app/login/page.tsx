"use client";

import { useEffect, useState, type FormEvent } from "react";

/** New path so a cached `/` (old knowledge list) or `/knowledge` is never reused. */
const POST_LOGIN_PATH = "/overview";

export default function LoginPage() {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const me = await fetch("/api/ops/me", { cache: "no-store" });
        if (me.ok) {
          window.location.replace(POST_LOGIN_PATH);
        }
      } catch {
        /* stay on login */
      }
    })();
  }, []);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError(null);
    try {
      const response = await fetch("/api/ops/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      if (!response.ok) {
        const body = (await response.json().catch(() => null)) as { detail?: string } | null;
        setError(body?.detail ?? `登录失败（${response.status}）`);
        return;
      }
      window.location.replace(POST_LOGIN_PATH);
    } catch {
      setError("无法连接运营后台服务");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="login-wrap">
      <form
        className="panel login-panel stack"
        method="POST"
        action="/api/ops/login"
        onSubmit={onSubmit}
      >
        <div>
          <span className="login-kicker">Operations</span>
          <div className="brand" style={{ marginBottom: 6 }}>
            AgentOS Ops
          </div>
          <p className="muted page-lead">知识审核、智能体发版、会话审计。</p>
        </div>
        <label>
          用户名
          <input
            name="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            autoComplete="username"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            enterKeyHint="next"
            required
          />
        </label>
        <label>
          密码
          <input
            name="password"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
            enterKeyHint="go"
            required
          />
        </label>
        {error ? <p className="error">{error}</p> : null}
        <button type="submit" className="block" disabled={pending}>
          {pending ? "登录中…" : "登录"}
        </button>
      </form>
    </div>
  );
}
