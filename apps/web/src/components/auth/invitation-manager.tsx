"use client";

import { useRef, useState } from "react";

type InvitationResponse = {
  email: string;
  invitation_url: string;
  expires_at: string;
};

function isInvitationResponse(value: unknown): value is InvitationResponse {
  return (
    typeof value === "object" &&
    value !== null &&
    "email" in value &&
    typeof value.email === "string" &&
    "invitation_url" in value &&
    typeof value.invitation_url === "string" &&
    "expires_at" in value &&
    typeof value.expires_at === "string"
  );
}

export function InvitationManager() {
  const emailInputRef = useRef<HTMLInputElement>(null);
  const [isOpen, setIsOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [invitation, setInvitation] = useState<InvitationResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [copied, setCopied] = useState(false);

  async function submitInvitation() {
    if (!emailInputRef.current?.reportValidity()) {
      return;
    }

    setIsSubmitting(true);
    setError(null);
    setCopied(false);

    try {
      const response = await fetch("/api/auth/invitations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      const payload: unknown = await response.json();

      if (!response.ok || !isInvitationResponse(payload)) {
        setError("无法创建邀请。请确认邮箱未注册且当前账户有邀请权限。");
        return;
      }

      setInvitation(payload);
      setEmail("");
    } catch {
      setError("暂时无法创建邀请，请稍后重试。");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function copyInvitationUrl() {
    if (invitation === null) {
      return;
    }

    try {
      await navigator.clipboard.writeText(invitation.invitation_url);
      setCopied(true);
    } catch {
      setError("无法自动复制，请手动复制邀请链接。");
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={() => setIsOpen(true)}
        className="shrink-0 border border-zinc-300 px-3 py-2 text-sm font-medium text-zinc-700 hover:border-zinc-500 hover:text-zinc-950"
      >
        邀请成员
      </button>

      {isOpen ? (
        <div
          className="fixed inset-0 z-50 grid place-items-center bg-zinc-950/30 p-4"
          role="presentation"
        >
          <section
            aria-labelledby="invite-title"
            className="w-full max-w-lg border border-zinc-200 bg-white p-5 shadow-xl sm:p-6"
            role="dialog"
            aria-modal="true"
          >
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 id="invite-title" className="text-lg font-semibold text-zinc-950">
                  创建邀请
                </h2>
                <p className="mt-1 text-sm text-zinc-600">生成后请将链接发送给对应成员。</p>
              </div>
              <button
                type="button"
                onClick={() => setIsOpen(false)}
                className="shrink-0 border border-zinc-300 px-2 py-1 text-sm text-zinc-700 hover:border-zinc-500"
                aria-label="关闭邀请窗口"
              >
                关闭
              </button>
            </div>

            <label className="mt-5 block text-sm font-medium text-zinc-800" htmlFor="invite-email">
              邮箱
            </label>
            <div className="mt-2 flex gap-2">
              <input
                id="invite-email"
                type="email"
                value={email}
                ref={emailInputRef}
                required
                onChange={(event) => setEmail(event.target.value)}
                placeholder="name@example.com"
                className="min-w-0 flex-1 border border-zinc-300 px-3 py-2 text-sm text-zinc-900 outline-none placeholder:text-zinc-400 focus:border-zinc-700"
              />
              <button
                type="button"
                onClick={() => void submitInvitation()}
                disabled={isSubmitting || !email.trim()}
                className="shrink-0 bg-zinc-950 px-3 py-2 text-sm font-medium text-white hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isSubmitting ? "创建中" : "创建"}
              </button>
            </div>

            {error ? (
              <p role="alert" className="mt-3 text-sm text-rose-700">
                {error}
              </p>
            ) : null}

            {invitation ? (
              <div className="mt-5 border border-zinc-200 bg-zinc-50 p-3">
                <p className="text-sm font-medium text-zinc-800">{invitation.email}</p>
                <p className="mt-1 text-xs text-zinc-500">
                  有效至 {new Date(invitation.expires_at).toLocaleString("zh-CN")}
                </p>
                <input
                  readOnly
                  value={invitation.invitation_url}
                  aria-label="邀请链接"
                  className="mt-3 w-full border border-zinc-300 bg-white px-2 py-2 font-mono text-xs text-zinc-700"
                />
                <button
                  type="button"
                  onClick={() => void copyInvitationUrl()}
                  className="mt-3 border border-zinc-300 px-3 py-2 text-sm font-medium text-zinc-700 hover:border-zinc-500"
                >
                  {copied ? "已复制" : "复制链接"}
                </button>
              </div>
            ) : null}
          </section>
        </div>
      ) : null}
    </>
  );
}
