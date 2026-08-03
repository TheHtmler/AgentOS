export type ThinkingStepState = {
  kind: "thinking";
  id: string;
  content: string;
  status: "running" | "done";
  expanded: boolean;
  afterMessageId: string;
};

/** Keep model reasoning private; expose only a compact activity state. */
export function ThinkingStepCard({ step }: { step: ThinkingStepState }) {
  const running = step.status === "running";
  const label = running ? "思考…" : "思考";

  return (
    <section className={`agentos-reasoning ${running ? "agentos-reasoning-running" : ""}`}>
      <div className="agentos-reasoning-head">
        <span className="agentos-reasoning-title">
          <span aria-hidden="true" className="agentos-reasoning-indicator" />
          {label}
        </span>
      </div>
    </section>
  );
}
