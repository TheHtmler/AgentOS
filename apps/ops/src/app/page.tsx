import { redirect } from "next/navigation";

import { OPS_SESSION_COOKIE_NAME, agentApiBaseUrl } from "@/lib/ops-api";
import { cookies } from "next/headers";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const token = (await cookies()).get(OPS_SESSION_COOKIE_NAME)?.value;
  if (!token) {
    redirect("/login");
  }

  try {
    const upstream = await fetch(`${agentApiBaseUrl()}/v1/ops/me`, {
      headers: { Cookie: `${OPS_SESSION_COOKIE_NAME}=${token}` },
      cache: "no-store",
    });
    if (!upstream.ok) {
      redirect("/login");
    }
  } catch {
    redirect("/login");
  }

  redirect("/knowledge");
}
