import { AuthenticatedWorkspace } from "@/components/auth/authenticated-workspace";

// The public entrypoint must not be retained as a year-long static page by a proxy cache.
export const dynamic = "force-dynamic";

export default function Home() {
  return <AuthenticatedWorkspace />;
}
