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

function stringField(record: Record<string, unknown>, key: string): string | null {
  const value = record[key];
  if (typeof value !== "string") {
    return null;
  }

  const trimmed = value.trim();
  return trimmed || null;
}

function queryFromArgsText(argsText: string): string | null {
  const trimmed = argsText.trim();
  if (!trimmed) {
    return null;
  }

  try {
    const parsed = JSON.parse(trimmed) as unknown;
    if (typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)) {
      return stringField(parsed as Record<string, unknown>, "query");
    }
  } catch {
    // Args may still be a partial JSON fragment while streaming.
  }

  return null;
}

function urlFromArgsText(argsText: string): string | null {
  const trimmed = argsText.trim();
  if (!trimmed) {
    return null;
  }

  try {
    const parsed = JSON.parse(trimmed) as unknown;
    if (typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)) {
      return stringField(parsed as Record<string, unknown>, "url");
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
  const url = urlFromArgsText(toolCall.argsText);
  const parts = [toolCall.toolName];

  if (query) {
    parts.push(query);
  } else if (url) {
    parts.push(url);
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
  const url = urlFromArgsText(toolCall.argsText);

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
          {url ? (
            <p>
              <span className="agentos-tool-call-label">url</span>
              {url}
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
      const provider = stringField(record, "provider") ?? undefined;

      if (typeof record.error === "string" && record.error.trim()) {
        return {
          summary: record.error.slice(0, 500),
          provider,
          status: "error",
        };
      }

      // fetch_url success shape
      if (typeof record.url === "string" && typeof record.text === "string") {
        const title = stringField(record, "title") ?? record.url;
        const truncated = record.truncated === true;
        const totalChars =
          typeof record.total_chars === "number" ? record.total_chars : record.text.length;
        const flag = truncated ? "truncated" : "full";
        return {
          summary: `${provider ?? "fetch_url"}: ${title} (${flag}, ${totalChars} chars)`.slice(
            0,
            500,
          ),
          provider,
          status: "done",
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
            return stringField(result, "title") ?? stringField(result, "url");
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

      // Parsed JSON without a structured error field is treated as success.
      if (provider) {
        return {
          summary: trimmed.slice(0, 500),
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
    // Do not substring-match "error" in page bodies — READMEs often contain that word.
    status: "done",
  };
}
