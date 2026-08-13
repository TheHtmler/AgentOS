import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { OpsShell } from "@/components/ops-shell";
import { OPS_SESSION_COOKIE_NAME, agentApiBaseUrl } from "@/lib/ops-api";

export const dynamic = "force-dynamic";

export default async function OpsLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const token = (await cookies()).get(OPS_SESSION_COOKIE_NAME)?.value;
  if (!token) {
    redirect("/login");
  }

  let subject = "ops";
  try {
    const upstream = await fetch(`${agentApiBaseUrl()}/v1/ops/me`, {
      headers: { Cookie: `${OPS_SESSION_COOKIE_NAME}=${token}` },
      cache: "no-store",
    });
    if (!upstream.ok) {
      redirect("/login");
    }
    const body = (await upstream.json()) as { subject?: string };
    subject = body.subject ?? subject;
  } catch {
    redirect("/login");
  }

  return <OpsShell subject={subject}>{children}</OpsShell>;
}
