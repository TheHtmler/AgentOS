"use client";

import { useLayoutEffect, useRef } from "react";

export type ThinkingStepState = {
  kind: "thinking";
  id: string;
  content: string;
  status: "running" | "done";
  expanded: boolean;
  afterMessageId: string;
};

/**
 * Compact thinking strip: ~2 visible lines while running so the user sees activity
 * without a tall panel. When done, only a one-line label remains.
 */
export function ThinkingStepCard({
  step,
  index,
}: {
  step: ThinkingStepState;
  index: number;
  onToggle: () => void;
}) {
  const previewRef = useRef<HTMLPreElement | null>(null);
  const running = step.status === "running";
  const label = running ? `思考 ${index}…` : `思考 ${index}`;

  useLayoutEffect(() => {
    const node = previewRef.current;
    if (!running || node === null) {
      return;
    }
    // Keep the newest thinking fragment visible in the 2-line window.
    node.scrollTop = node.scrollHeight;
  }, [running, step.content]);

  return (
    <section
      className={`agentos-reasoning ${running ? "agentos-reasoning-running" : ""}`}
    >
      <div className="agentos-reasoning-head">
        <span className="agentos-reasoning-title">
          <span aria-hidden="true" className="agentos-reasoning-indicator" />
          {label}
        </span>
      </div>
      {running && step.content ? (
        <pre ref={previewRef} className="agentos-reasoning-content">
          {step.content}
        </pre>
      ) : null}
    </section>
  );
}
