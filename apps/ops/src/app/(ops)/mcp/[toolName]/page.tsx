import { redirect } from "next/navigation";

type PageProps = {
  params: Promise<{ toolName: string }>;
};

export default async function McpToolDetailPage({ params }: PageProps) {
  const { toolName } = await params;
  redirect(`/tools/${encodeURIComponent(toolName)}`);
}
