import { useEffect, useState } from "react";

export type ThinkingStepState = {
  kind: "thinking";
  id: string;
  content: string;
  phase: string;
  contentMode: "none" | "text" | "encrypted";
  status: "running" | "done";
  startedAt: number;
  durationMs?: number;
  expanded: boolean;
  afterMessageId: string;
};

export function ThinkingStepCard({ step }: { step: ThinkingStepState }) {
  const running = step.status === "running";
  const label = running ? step.phase : "思考完成";
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
      {step.content ? (
        <pre className="agentos-reasoning-content">{step.content}</pre>
      ) : step.contentMode === "encrypted" ? (
        <p className="agentos-reasoning-content">模型返回了加密 reasoning，当前无法展示文本</p>
      ) : !running ? (
        <p className="agentos-reasoning-content">该模型未返回可展示的 reasoning 内容</p>
      ) : null}
    </section>
  );
}
