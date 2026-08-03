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
  const title =
    step.status === "done" ? `Thinking #${index}` : `Thinking #${index}...`;

  return (
    <section
      className={`agentos-reasoning max-w-[92%] sm:max-w-[85%] ${
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
        <span className="agentos-reasoning-state">
          {step.status === "done" ? "已完成" : "运行中"} · {step.expanded ? "收起" : "展开"}
        </span>
      </button>

      {step.expanded && step.content ? (
        <pre className="agentos-reasoning-content">{step.content}</pre>
      ) : null}
    </section>
  );
}
