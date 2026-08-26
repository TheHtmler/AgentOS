export type AgentSummary = {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  kind: "general" | "vertical";
  is_default: boolean;
  memory_enabled: boolean;
  case_enabled: boolean;
  supports_vision: boolean;
};

/** 后端内置 General 的稳定 ID，避免首屏在助手列表返回前出现空选择。 */
export const GENERAL_AGENT_ID = "00000000-0000-0000-0000-000000000001";

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
    typeof value.case_enabled === "boolean" &&
    typeof value.supports_vision === "boolean"
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

export function resolveSelectedAgentId(
  currentAgentId: string | null,
  agents: AgentSummary[],
): string {
  if (currentAgentId !== null && agents.some((agent) => agent.id === currentAgentId)) {
    return currentAgentId;
  }

  return (
    agents.find((agent) => agent.slug === "general")?.id ??
    agents.find((agent) => agent.is_default)?.id ??
    agents[0]?.id ??
    GENERAL_AGENT_ID
  );
}

export function displayAgentName(name: string): string {
  return name
    .replace(/\bGeneral Agent\b/gi, "通用助手")
    .replace(/\bGeneral\b/gi, "通用助手")
    .replace(/\bAgent\b/gi, "助手")
    .trim();
}
