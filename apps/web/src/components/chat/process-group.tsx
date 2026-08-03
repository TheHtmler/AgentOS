"use client";

import { useState, type ReactNode } from "react";

/**
 * Codex-style wrapper: Thinking + tool rows collapse into "已处理" after the turn ends.
 * While the run is active the group stays expanded; on active→idle it auto-collapses.
 */
export function ProcessGroup({
  isActive,
  durationLabel,
  children,
}: {
  isActive: boolean;
  durationLabel?: string | null;
  children: ReactNode;
}) {
  const [expanded, setExpanded] = useState(isActive);
  const [prevActive, setPrevActive] = useState(isActive);

  // Sync open/closed when the turn starts or finishes (React "adjust state while rendering").
  if (isActive !== prevActive) {
    setPrevActive(isActive);
    setExpanded(isActive);
  }

  const title = isActive
    ? "处理中…"
    : durationLabel
      ? `已处理 ${durationLabel}`
      : "已处理";

  return (
    <section
      className={`agentos-process-group max-w-full sm:max-w-[92%] ${
        isActive ? "agentos-process-group-active" : ""
      }`}
    >
      <button
        type="button"
        onClick={() => setExpanded((current) => !current)}
        aria-expanded={expanded}
        className="agentos-process-group-toggle"
      >
        <span className="agentos-process-group-title">{title}</span>
        <span className="agentos-process-group-action">
          <span aria-hidden="true" className="agentos-process-group-chevron">
            {expanded ? "▼" : "▶"}
          </span>
          <span className="agentos-process-group-action-label">
            {expanded ? "收起" : "展开"}
          </span>
        </span>
      </button>
      {expanded ? <div className="agentos-process-group-body">{children}</div> : null}
    </section>
  );
}
