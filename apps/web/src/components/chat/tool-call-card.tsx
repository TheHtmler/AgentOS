"use client";

export type ToolCallStatus = "running" | "done" | "error";

export type ToolCallState = {
  id: string;
  toolName: string;
  argsText: string;
  status: ToolCallStatus;
  resultSummary?: string;
  provider?: string;
  expanded: boolean;
  afterMessageId: string;
};

function queryFromArgsText(argsText: string): string | null {
  const trimmed = argsText.trim();
  if (!trimmed) {
    return null;
  }

  try {
    const parsed = JSON.parse(trimmed) as unknown;
    if (
      typeof parsed === "object" &&
      parsed !== null &&
      !Array.isArray(parsed) &&
      "query" in parsed &&
      typeof (parsed as { query: unknown }).query === "string"
    ) {
      const query = (parsed as { query: string }).query.trim();
      return query || null;
    }
  } catch {
    // Args may still be a partial JSON fragment while streaming.
  }

  return null;
}

function statusLabel(status: ToolCallStatus): string {
  if (status === "running") {
    return "运行中";
  }

  if (status === "error") {
    return "失败";
  }

  return "完成";
}

function collapsedSummary(toolCall: ToolCallState): string {
  const query = queryFromArgsText(toolCall.argsText);
  const parts = [toolCall.toolName];

  if (query) {
    parts.push(query);
  }

  const status = statusLabel(toolCall.status);
  if (toolCall.provider && toolCall.status !== "running") {
    parts.push(`${status}（${toolCall.provider}）`);
  } else {
    parts.push(status);
  }

  return parts.join(" · ");
}

export function ToolCallCard({
  toolCall,
  onToggle,
}: {
  toolCall: ToolCallState;
  onToggle: () => void;
}) {
  const query = queryFromArgsText(toolCall.argsText);

  return (
    <section
      className={`agentos-tool-call max-w-[92%] sm:max-w-[85%] ${
        toolCall.status === "running" ? "agentos-tool-call-running" : ""
      } ${toolCall.status === "error" ? "agentos-tool-call-error" : ""}`}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={toolCall.expanded}
        className="agentos-tool-call-toggle"
      >
        <span className="agentos-tool-call-title">
          <span aria-hidden="true" className="agentos-tool-call-indicator" />
          {collapsedSummary(toolCall)}
        </span>
        <span className="agentos-tool-call-state">
          {toolCall.expanded ? "收起" : "展开"}
        </span>
      </button>

      {toolCall.expanded ? (
        <div className="agentos-tool-call-content">
          {query ? (
            <p>
              <span className="agentos-tool-call-label">query</span>
              {query}
            </p>
          ) : null}
          {toolCall.argsText.trim() ? (
            <pre>{toolCall.argsText.trim()}</pre>
          ) : null}
          {toolCall.provider ? (
            <p>
              <span className="agentos-tool-call-label">provider</span>
              {toolCall.provider}
            </p>
          ) : null}
          {toolCall.resultSummary ? (
            <p>
              <span className="agentos-tool-call-label">
                {toolCall.status === "error" ? "error" : "result"}
              </span>
              {toolCall.resultSummary}
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

export function summarizeToolResultContent(content: string): {
  summary: string;
  provider?: string;
  status: ToolCallStatus;
} {
  const trimmed = content.trim();

  try {
    const parsed = JSON.parse(trimmed) as unknown;
    if (typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)) {
      const record = parsed as Record<string, unknown>;
      const provider = typeof record.provider === "string" ? record.provider : undefined;

      if (typeof record.error === "string" && record.error.trim()) {
        return {
          summary: record.error.slice(0, 500),
          provider,
          status: "error",
        };
      }

      if (Array.isArray(record.results)) {
        const titles = record.results
          .slice(0, 3)
          .map((item) => {
            if (typeof item !== "object" || item === null) {
              return null;
            }

            const result = item as Record<string, unknown>;
            if (typeof result.title === "string" && result.title.trim()) {
              return result.title.trim();
            }

            if (typeof result.url === "string" && result.url.trim()) {
              return result.url.trim();
            }

            return null;
          })
          .filter((title): title is string => title !== null);

        const count = record.results.length;
        const head = `${provider ?? "search"}: ${count} results`;
        const summary = titles.length > 0 ? `${head}; ${titles.join("; ")}` : head;
        return {
          summary: summary.slice(0, 500),
          provider,
          status: "done",
        };
      }
    }
  } catch {
    // Fall through to a truncated raw summary.
  }

  return {
    summary: trimmed.slice(0, 500),
    status: trimmed.toLowerCase().includes("error") ? "error" : "done",
  };
}
