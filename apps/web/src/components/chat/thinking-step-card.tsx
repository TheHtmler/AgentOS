"use client";

export type ThinkingStepState = {
  kind: "thinking";
  id: string;
  content: string;
  status: "running" | "done";
  expanded: boolean;
  afterMessageId: string;
};

export function ThinkingStepCard({
  step,
  index,
  onToggle,
}: {
  step: ThinkingStepState;
  index: number;
  onToggle: () => void;
}) {
  const title = step.status === "done" ? `思考 ${index}` : `思考 ${index}…`;

  return (
    <section
      className={`agentos-reasoning ${
        step.status === "running" ? "agentos-reasoning-running" : ""
      }`}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={step.expanded}
        className="agentos-reasoning-toggle"
      >
        <span className="agentos-reasoning-title">
          <span aria-hidden="true" className="agentos-reasoning-indicator" />
          {title}
        </span>
        <span className="agentos-reasoning-state" aria-hidden="true">
          {step.expanded ? "▾" : "▸"}
        </span>
      </button>

      {step.expanded && step.content ? (
        <pre className="agentos-reasoning-content">{step.content}</pre>
      ) : null}
    </section>
  );
}
