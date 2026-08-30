"use client";

import { useCallback, useEffect, useState } from "react";
import { Link2, MessageCircle, RefreshCw, Unlink } from "lucide-react";

import { Button } from "@/components/ui/button";

type UserChannelBinding = {
  id: string;
  channel: string;
  display_name: string;
  status: string;
  receive_notifications: boolean;
  is_default: boolean;
  last_verified_at: string | null;
  created_at: string;
  updated_at: string;
};

type BindingListResponse = {
  bindings: UserChannelBinding[];
};

type WeixinQrLogin = {
  id: string;
  status: "pending" | "completed" | "failed";
  qrcode_url: string | null;
  expires_at: string | null;
  error: string | null;
};

function errorMessage(payload: unknown): string | null {
  if (typeof payload !== "object" || payload === null) return null;
  if ("detail" in payload && typeof payload.detail === "string") return payload.detail;
  if ("error" in payload && typeof payload.error === "string") return payload.error;
  return null;
}

async function responseError(response: Response, fallback: string): Promise<Error> {
  try {
    const payload: unknown = await response.json();
    return new Error(errorMessage(payload) ?? fallback);
  } catch {
    return new Error(fallback);
  }
}

function formatExpiry(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? ""
    : `有效期至 ${date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}`;
}

export function WeChatBindingPanel() {
  const [bindings, setBindings] = useState<UserChannelBinding[]>([]);
  const [qrLogin, setQrLogin] = useState<WeixinQrLogin | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadBindings = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch("/api/channel-bindings", {
        cache: "no-store",
      });
      if (!response.ok) throw await responseError(response, "无法加载微信绑定状态。");
      const payload = (await response.json()) as BindingListResponse;
      setBindings(Array.isArray(payload.bindings) ? payload.bindings : []);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "无法加载微信绑定状态。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    /* eslint-disable react-hooks/set-state-in-effect */
    void loadBindings();
    /* eslint-enable react-hooks/set-state-in-effect */
  }, [loadBindings]);

  const startQrLogin = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const response = await fetch("/api/channel-bindings/weixin-login", { method: "POST" });
      if (!response.ok) throw await responseError(response, "二维码生成失败。");
      setQrLogin((await response.json()) as WeixinQrLogin);
    } catch (createError) {
      setError(createError instanceof Error ? createError.message : "二维码生成失败。");
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    if (qrLogin?.status !== "pending") return;
    const timer = window.setInterval(() => {
      void fetch(`/api/channel-bindings/weixin-login/${qrLogin.id}`, { cache: "no-store" })
        .then((response) => (response.ok ? response.json() : null))
        .then((value: unknown) => {
          if (typeof value !== "object" || value === null || !("status" in value)) return;
          const next = value as WeixinQrLogin;
          setQrLogin(next);
          if (next.status === "completed") void loadBindings();
          if (next.status === "failed")
            setError(next.error ?? "微信连接未完成，请重新生成二维码。");
        });
    }, 2_000);
    return () => window.clearInterval(timer);
  }, [loadBindings, qrLogin]);

  const revokeBinding = useCallback(
    async (binding: UserChannelBinding) => {
      if (confirmingId !== binding.id) {
        setConfirmingId(binding.id);
        return;
      }
      setBusy(true);
      setError(null);
      try {
        const response = await fetch(`/api/channel-bindings/${binding.id}`, { method: "DELETE" });
        if (!response.ok) throw await responseError(response, "解绑失败。");
        setConfirmingId(null);
        await loadBindings();
      } catch (revokeError) {
        setError(revokeError instanceof Error ? revokeError.message : "解绑失败。");
      } finally {
        setBusy(false);
      }
    },
    [confirmingId, loadBindings],
  );

  const activeBindings = bindings.filter((binding) => binding.status === "active");

  return (
    <section className="mx-auto flex h-full w-full max-w-3xl flex-col overflow-y-auto px-5 py-8 sm:px-8">
      <header className="border-b border-border pb-6">
        <div className="flex items-center gap-3">
          <span className="grid size-10 place-items-center rounded-md bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">
            <MessageCircle aria-hidden="true" className="size-5" />
          </span>
          <div>
            <p className="text-xs font-medium tracking-[0.16em] text-muted-foreground uppercase">
              Channel
            </p>
            <h1 className="text-xl font-semibold text-foreground">微信通知</h1>
          </div>
        </div>
        <p className="mt-4 max-w-xl text-sm leading-6 text-muted-foreground">
          将当前 AgentOS 账号连接到你的微信会话。OpenClaw 只负责收发消息，绑定不经过模型。
        </p>
      </header>

      <div className="space-y-5 py-6">
        <section className="border border-border bg-card p-5 shadow-sm">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
                <Link2 aria-hidden="true" className="size-4" />
                {activeBindings.length > 0 ? "微信已连接" : "连接微信"}
              </h2>
              <p className="mt-2 text-sm leading-6 text-muted-foreground">
                使用微信扫描二维码，即可连接当前 AgentOS 账号。
              </p>
            </div>
            <Button type="button" onClick={() => void startQrLogin()} disabled={busy}>
              <RefreshCw aria-hidden="true" className="size-4" />
              {busy ? "处理中" : "生成二维码"}
            </Button>
          </div>

          {qrLogin?.status === "pending" && qrLogin.qrcode_url ? (
            <div className="mt-5 flex flex-wrap items-center gap-5 border-t border-border pt-5">
              <img
                src={qrLogin.qrcode_url}
                alt="微信连接二维码"
                className="size-44 border border-border bg-white p-2"
              />
              <div className="text-sm text-muted-foreground">
                <p>请使用微信扫码并确认。</p>
                {qrLogin.expires_at ? (
                  <p className="mt-2">{formatExpiry(qrLogin.expires_at)}</p>
                ) : null}
              </div>
            </div>
          ) : null}
          {qrLogin?.status === "completed" ? (
            <p className="mt-5 border-t border-border pt-5 text-sm text-emerald-700 dark:text-emerald-300">
              微信已连接，可接收定时任务通知。
            </p>
          ) : null}
        </section>

        <section className="border border-border bg-card p-5 shadow-sm">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-foreground">当前绑定</h2>
              <p className="mt-1 text-xs text-muted-foreground">
                仅显示当前 AgentOS 账号的微信连接。
              </p>
            </div>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              onClick={() => void loadBindings()}
              disabled={loading}
              aria-label="刷新绑定状态"
              title="刷新绑定状态"
            >
              <RefreshCw aria-hidden="true" className="size-4" />
            </Button>
          </div>

          {loading ? <p className="mt-5 text-sm text-muted-foreground">加载中...</p> : null}
          {!loading && bindings.length === 0 ? (
            <p className="mt-5 text-sm text-muted-foreground">尚未连接微信。</p>
          ) : null}
          {!loading && bindings.length > 0 ? (
            <div className="mt-5 space-y-3">
              {bindings.map((binding) => (
                <div
                  key={binding.id}
                  className="flex flex-wrap items-center justify-between gap-3 border border-border px-4 py-3"
                >
                  <div>
                    <p className="text-sm font-medium text-foreground">微信会话</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {binding.status === "active" ? "通知已启用" : "已停用"}
                      {binding.is_default ? " · 默认收件人" : ""}
                    </p>
                  </div>
                  {binding.status === "active" ? (
                    <Button
                      type="button"
                      variant={confirmingId === binding.id ? "destructive" : "outline"}
                      size="sm"
                      onClick={() => void revokeBinding(binding)}
                      disabled={busy}
                    >
                      <Unlink aria-hidden="true" className="size-4" />
                      {confirmingId === binding.id ? "确认解绑" : "解绑微信"}
                    </Button>
                  ) : null}
                </div>
              ))}
            </div>
          ) : null}
        </section>

        {error ? (
          <p
            className="border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive"
            role="alert"
          >
            {error}
          </p>
        ) : null}
      </div>
    </section>
  );
}
