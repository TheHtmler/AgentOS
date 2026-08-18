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

export type CaseFact = {
  id: string;
  key: string | null;
  content: string;
  tags: string[];
  status: string;
};

function isCaseFact(value: unknown): value is CaseFact {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    (typeof value.key === "string" || value.key === null) &&
    typeof value.content === "string" &&
    Array.isArray(value.tags) &&
    value.tags.every((tag) => typeof tag === "string") &&
    typeof value.status === "string"
  );
}

export function parseCaseFacts(value: unknown): CaseFact[] | null {
  if (!isRecord(value) || !Array.isArray(value.facts)) {
    return null;
  }

  const facts: CaseFact[] = [];
  for (const item of value.facts) {
    if (!isCaseFact(item)) {
      return null;
    }
    facts.push(item);
  }
  return facts;
}
