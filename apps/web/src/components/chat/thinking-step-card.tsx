export type ThinkingStepState = {
  kind: "thinking";
  id: string;
  content: string;
  status: "running" | "done";
  expanded: boolean;
  afterMessageId: string;
};

export function ThinkingStepCard({ step }: { step: ThinkingStepState }) {
  const running = step.status === "running";
  const label = running ? "正在处理" : "已完成处理";

  return (
    <section
      className={`agentos-reasoning ${running ? "agentos-reasoning-running" : ""}`}
      aria-live="polite"
    >
      <div className="agentos-reasoning-head">
        <span className="agentos-reasoning-title">
          <span aria-hidden="true" className="agentos-reasoning-indicator" />
          {label}
        </span>
      </div>
      {step.content ? <p className="agentos-reasoning-content">{step.content}</p> : null}
    </section>
  );
}
