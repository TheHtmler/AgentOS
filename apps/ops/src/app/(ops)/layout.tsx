import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { OpsShell } from "@/components/ops-shell";
import { OPS_SESSION_COOKIE_NAME, agentApiBaseUrl } from "@/lib/ops-api";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export default async function OpsLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const token = (await cookies()).get(OPS_SESSION_COOKIE_NAME)?.value;
  if (!token) {
    redirect("/login");
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${agentApiBaseUrl()}/v1/ops/me`, {
      headers: { Cookie: `${OPS_SESSION_COOKIE_NAME}=${token}` },
      cache: "no-store",
    });
  } catch {
    // Network failure (agent-api mid-restart, e.g. during deploy) is transient,
    // not an auth problem — bouncing to /login would force a needless re-login.
    return <OpsUpstreamUnavailable />;
  }

  if (upstream.status === 401) {
    redirect("/login");
  }
  if (!upstream.ok) {
    return <OpsUpstreamUnavailable />;
  }

  const body = (await upstream.json()) as { subject?: string };
  const subject = body.subject ?? "ops";
  return <OpsShell subject={subject}>{children}</OpsShell>;
}

function OpsUpstreamUnavailable() {
  return (
    <div className="login-wrap">
      <div className="panel login-panel stack">
        <div>
          <span className="login-kicker">Operations</span>
          <div className="brand" style={{ marginBottom: 6 }}>
            AgentOS Ops
          </div>
          <p className="muted page-lead">后台服务暂时不可用。</p>
        </div>
        <p className="muted">agent-api 可能正在重启（例如部署中），请稍后刷新重试。</p>
      </div>
    </div>
  );
}
