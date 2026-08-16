"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";

function subscribeNoop() {
  return () => {};
}

function useIsClient() {
  return useSyncExternalStore(
    subscribeNoop,
    () => true,
    () => false,
  );
}

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
  const isClient = useIsClient();

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [isOpen]);

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

  const dialog =
    isOpen && isClient
      ? createPortal(
          <div
            className="agentos-invite-overlay"
            role="presentation"
            onClick={(event) => {
              if (event.target === event.currentTarget) {
                setIsOpen(false);
              }
            }}
          >
            <section
              aria-labelledby="invite-title"
              className="agentos-invite-dialog"
              role="dialog"
              aria-modal="true"
            >
              <div className="agentos-invite-header">
                <div className="agentos-invite-header-copy">
                  <h2 id="invite-title" className="agentos-invite-title">
                    创建邀请
                  </h2>
                  <p className="agentos-invite-desc">生成后请将链接发送给对应成员。</p>
                </div>
                <button
                  type="button"
                  onClick={() => setIsOpen(false)}
                  className="agentos-invite-secondary agentos-invite-close"
                  aria-label="关闭邀请窗口"
                >
                  关闭
                </button>
              </div>

              <label className="agentos-invite-label" htmlFor="invite-email">
                邮箱
              </label>
              <div className="agentos-invite-form-row">
                <input
                  id="invite-email"
                  type="email"
                  value={email}
                  ref={emailInputRef}
                  required
                  onChange={(event) => setEmail(event.target.value)}
                  placeholder="name@example.com"
                  className="agentos-invite-input"
                />
                <button
                  type="button"
                  onClick={() => void submitInvitation()}
                  disabled={isSubmitting || !email.trim()}
                  className="agentos-invite-primary"
                >
                  {isSubmitting ? "创建中" : "创建"}
                </button>
              </div>

              {error ? (
                <p role="alert" className="agentos-invite-error">
                  {error}
                </p>
              ) : null}

              {invitation ? (
                <div className="agentos-invite-result">
                  <p className="agentos-invite-label">{invitation.email}</p>
                  <p className="agentos-invite-desc agentos-invite-desc-small">
                    有效至 {new Date(invitation.expires_at).toLocaleString("zh-CN")}
                  </p>
                  <input
                    readOnly
                    value={invitation.invitation_url}
                    aria-label="邀请链接"
                    className="agentos-invite-url"
                  />
                  <button
                    type="button"
                    onClick={() => void copyInvitationUrl()}
                    className="agentos-invite-secondary agentos-invite-copy"
                  >
                    {copied ? "已复制" : "复制链接"}
                  </button>
                </div>
              ) : null}
            </section>
          </div>,
          document.body,
        )
      : null;

  return (
    <>
      <button type="button" onClick={() => setIsOpen(true)} className="agentos-invite-trigger">
        邀请成员
      </button>
      {dialog}
    </>
  );
}
