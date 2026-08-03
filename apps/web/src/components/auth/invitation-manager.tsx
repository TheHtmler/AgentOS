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
        className="agentos-invite-trigger shrink-0 px-3 py-2 text-sm font-medium"
      >
        邀请成员
      </button>

      {isOpen ? (
        <div
          className="agentos-invite-overlay fixed inset-0 z-50 overflow-y-auto p-4 pb-[calc(1rem+env(safe-area-inset-bottom))]"
          role="presentation"
          onClick={(event) => {
            if (event.target === event.currentTarget) {
              setIsOpen(false);
            }
          }}
        >
          <section
            aria-labelledby="invite-title"
            className="agentos-invite-dialog mx-auto my-6 w-full max-w-lg"
            role="dialog"
            aria-modal="true"
          >
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <h2 id="invite-title" className="agentos-invite-title text-lg font-semibold">
                  创建邀请
                </h2>
                <p className="agentos-invite-desc mt-1 text-sm">
                  生成后请将链接发送给对应成员。
                </p>
              </div>
              <button
                type="button"
                onClick={() => setIsOpen(false)}
                className="agentos-invite-secondary shrink-0 px-2 py-1 text-sm"
                aria-label="关闭邀请窗口"
              >
                关闭
              </button>
            </div>

            <label className="agentos-invite-label mt-5 block text-sm font-medium" htmlFor="invite-email">
              邮箱
            </label>
            <div className="mt-2 flex flex-col gap-2 sm:flex-row">
              <input
                id="invite-email"
                type="email"
                value={email}
                ref={emailInputRef}
                required
                onChange={(event) => setEmail(event.target.value)}
                placeholder="name@example.com"
                className="agentos-invite-input min-w-0 w-full flex-1 px-3 py-2 text-sm outline-none sm:w-auto"
              />
              <button
                type="button"
                onClick={() => void submitInvitation()}
                disabled={isSubmitting || !email.trim()}
                className="agentos-invite-primary w-full shrink-0 px-3 py-2 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-60 sm:w-auto"
              >
                {isSubmitting ? "创建中" : "创建"}
              </button>
            </div>

            {error ? (
              <p role="alert" className="agentos-invite-error mt-3 text-sm">
                {error}
              </p>
            ) : null}

            {invitation ? (
              <div className="agentos-invite-result mt-5 p-3">
                <p className="agentos-invite-label text-sm font-medium">{invitation.email}</p>
                <p className="agentos-invite-desc mt-1 text-xs">
                  有效至 {new Date(invitation.expires_at).toLocaleString("zh-CN")}
                </p>
                <input
                  readOnly
                  value={invitation.invitation_url}
                  aria-label="邀请链接"
                  className="agentos-invite-url mt-3 w-full px-2 py-2 font-mono text-xs break-all"
                />
                <button
                  type="button"
                  onClick={() => void copyInvitationUrl()}
                  className="agentos-invite-secondary mt-3 w-full px-3 py-2 text-sm font-medium sm:w-auto"
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
