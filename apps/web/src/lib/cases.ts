export type CaseSummary = {
  id: string;
  display_name: string;
  status: string;
  is_default: boolean;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isCaseSummary(value: unknown): value is CaseSummary {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.display_name === "string" &&
    typeof value.status === "string" &&
    typeof value.is_default === "boolean"
  );
}

export function parseCaseSummaries(value: unknown): CaseSummary[] | null {
  if (!isRecord(value) || !Array.isArray(value.cases)) {
    return null;
  }

  const cases: CaseSummary[] = [];
  for (const item of value.cases) {
    if (!isCaseSummary(item)) {
      return null;
    }
    cases.push(item);
  }
  return cases;
}
