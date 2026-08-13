"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

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
      router.replace("/");
      router.refresh();
    } catch {
      setError("无法连接运营后台服务");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="login-wrap">
      <form className="panel login-panel stack" onSubmit={onSubmit}>
        <div>
          <span className="login-kicker">Operations</span>
          <div className="brand-mark" style={{ marginBottom: 8 }}>
            <span className="brand-mark__glyph" aria-hidden />
            <div className="brand">AgentOS Ops</div>
          </div>
          <p className="muted page-lead">运营控制台 · 知识审核与智能体配置（非聊天站）</p>
        </div>
        <label>
          用户名
          <input
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
