"use client";

import { useCallback, useEffect, useState } from "react";

import { ChatWorkspace } from "@/components/chat/chat-workspace";

type CurrentUser = {
  id: string;
  email: string;
  can_manage_invitations: boolean;
};

type AuthenticationState =
  | { kind: "checking" }
  | { kind: "authenticated"; user: CurrentUser }
  | { kind: "unavailable" };

function isCurrentUser(value: unknown): value is CurrentUser {
  return (
    typeof value === "object" &&
    value !== null &&
    "id" in value &&
    typeof value.id === "string" &&
    "email" in value &&
    typeof value.email === "string" &&
    "can_manage_invitations" in value &&
    typeof value.can_manage_invitations === "boolean"
  );
}

export function AuthenticatedWorkspace() {
  const [state, setState] = useState<AuthenticationState>({ kind: "checking" });
  const [isLoggingOut, setIsLoggingOut] = useState(false);

  useEffect(() => {
    const controller = new AbortController();

    void (async () => {
      const maxAttempts = 3;

      for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
        try {
          const response = await fetch("/api/auth/me", {
            cache: "no-store",
            signal: controller.signal,
          });

          if (response.status === 401) {
            window.location.replace("/login");
            return;
          }

          const payload: unknown = await response.json();

          if (response.ok && isCurrentUser(payload)) {
            setState({ kind: "authenticated", user: payload });
            return;
          }

          // Transient Agent API blips should not strand the workspace after actions like delete.
          if (attempt < maxAttempts && (response.status === 502 || response.status === 503)) {
            await new Promise((resolve) => window.setTimeout(resolve, 300 * attempt));
            continue;
          }

          setState({ kind: "unavailable" });
          return;
        } catch {
          if (controller.signal.aborted) {
            return;
          }

          if (attempt < maxAttempts) {
            await new Promise((resolve) => window.setTimeout(resolve, 300 * attempt));
            continue;
          }

          setState({ kind: "unavailable" });
          return;
        }
      }
    })();

    return () => controller.abort();
  }, []);

  const logout = useCallback(async () => {
    setIsLoggingOut(true);

    try {
      await fetch("/api/auth/logout", { method: "POST" });
    } finally {
      window.location.replace("/login");
    }
  }, []);

  if (state.kind === "checking") {
    return <main className="agentos-app" aria-busy="true" />;
  }

  if (state.kind === "unavailable") {
    return (
      <main className="agentos-app grid min-h-screen place-items-center px-4">
        <section className="w-full max-w-md border border-zinc-200 bg-white p-6">
          <h1 className="text-lg font-semibold text-zinc-950">无法验证登录状态</h1>
          <p className="mt-2 text-sm text-zinc-600">请确认 Agent API 可用后刷新页面。</p>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="agentos-header-action mt-5 border px-3 py-2 text-sm font-medium"
          >
            刷新
          </button>
        </section>
      </main>
    );
  }

  return (
    <main className="agentos-app min-w-0 overflow-x-hidden">
      <ChatWorkspace
        userEmail={state.user.email}
        canManageInvitations={state.user.can_manage_invitations}
        isLoggingOut={isLoggingOut}
        onLogout={() => void logout()}
      />
    </main>
  );
}