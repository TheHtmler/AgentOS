import { useEffect, useState } from "react";

export type ThinkingStepState = {
  kind: "thinking";
  id: string;
  content: string;
  status: "running" | "done";
  startedAt: number;
  durationMs?: number;
  expanded: boolean;
  afterMessageId: string;
};

export function ThinkingStepCard({ step }: { step: ThinkingStepState }) {
  const running = step.status === "running";
  const label = running ? "正在处理" : "已完成处理";
  const [now, setNow] = useState<number | null>(null);

  useEffect(() => {
    if (!running) {
      return;
    }
    const update = () => setNow(Date.now());
    update();
    const timer = window.setInterval(update, 250);
    return () => window.clearInterval(timer);
  }, [running]);

  const durationMs =
    step.durationMs ?? (running && now !== null ? Math.max(0, now - step.startedAt) : undefined);
  const durationLabel =
    durationMs === undefined
      ? null
      : durationMs < 1000
        ? `${durationMs}ms`
        : `${(durationMs / 1000).toFixed(1)}s`;

  return (
    <section
      className={`agentos-reasoning ${running ? "agentos-reasoning-running" : ""}`}
      aria-live="polite"
    >
      <div className="agentos-reasoning-head">
        <span className="agentos-reasoning-title">
          <span aria-hidden="true" className="agentos-reasoning-indicator" />
          {label}
          {durationLabel ? (
            <span className="agentos-reasoning-duration">{durationLabel}</span>
          ) : null}
        </span>
      </div>
      {step.content ? <p className="agentos-reasoning-content">{step.content}</p> : null}
    </section>
  );
}
