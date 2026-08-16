"use client";

import { FormEvent, useState } from "react";

import { ThemeToggle } from "@/components/theme/theme-toggle";

export default function LoginPage() {
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);

    const form = new FormData(event.currentTarget);
    const response = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: form.get("email"),
        password: form.get("password"),
      }),
    });

    if (!response.ok) {
      setError("邮箱或密码错误。");
      setSubmitting(false);
      return;
    }

    // Cookie 已由同源 BFF 写入，浏览器不接触 session token。
    window.location.replace("/");
  }

  return (
    <main className="agentos-auth-shell relative grid place-items-center overflow-auto px-4">
      <div className="absolute top-4 right-4">
        <ThemeToggle />
      </div>
      <form onSubmit={submit} className="agentos-auth-card w-full max-w-md p-6">
        <p className="text-sm font-medium text-zinc-500">AgentOS</p>
        <h1 className="mt-2 text-xl font-semibold text-zinc-950">登录</h1>

        <label className="mt-6 block text-sm font-medium text-zinc-800">
          邮箱
          <input
            required
            name="email"
            type="email"
            autoComplete="email"
            className="agentos-auth-input mt-2 w-full px-3 py-2"
          />
        </label>

        <label className="mt-4 block text-sm font-medium text-zinc-800">
          密码
          <input
            required
            name="password"
            type="password"
            autoComplete="current-password"
            className="agentos-auth-input mt-2 w-full px-3 py-2"
          />
        </label>

        {error ? (
          <p role="alert" className="mt-4 text-sm text-rose-700">
            {error}
          </p>
        ) : null}

        <button
          disabled={submitting}
          className="agentos-auth-submit mt-6 w-full px-4 py-2.5 text-sm disabled:opacity-60"
        >
          {submitting ? "正在登录" : "登录"}
        </button>
      </form>
    </main>
  );
}
