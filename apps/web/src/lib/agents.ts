export type AgentSummary = {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  kind: "general" | "vertical";
  is_default: boolean;
  memory_enabled: boolean;
  case_enabled: boolean;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isAgentSummary(value: unknown): value is AgentSummary {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.slug === "string" &&
    typeof value.name === "string" &&
    (typeof value.description === "string" || value.description === null) &&
    (value.kind === "general" || value.kind === "vertical") &&
    typeof value.is_default === "boolean" &&
    typeof value.memory_enabled === "boolean" &&
    typeof value.case_enabled === "boolean"
  );
}

export function parseAgentSummaries(value: unknown): AgentSummary[] | null {
  if (!isRecord(value) || !Array.isArray(value.agents)) {
    return null;
  }

  const agents: AgentSummary[] = [];
  for (const agent of value.agents) {
    if (!isAgentSummary(agent)) {
      return null;
    }
    agents.push(agent);
  }

  return agents;
}
