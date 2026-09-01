import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

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
  const [expanded, setExpanded] = useState(running);
  const [previousRunning, setPreviousRunning] = useState(running);

  // A live step stays open, then settles back into the compact timeline.
  if (running !== previousRunning) {
    setPreviousRunning(running);
    setExpanded(running);
  }

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

  // Providers that return opaque reasoning (no streamed text) leave nothing
  // worth showing once the step ends — hide the card instead of a placeholder.
  if (!running && !step.content) {
    return null;
  }

  return (
    <section
      className={`agentos-reasoning ${running ? "agentos-reasoning-running" : ""}`}
      aria-live="polite"
    >
      <button
        type="button"
        className="agentos-reasoning-head"
        onClick={() => setExpanded((current) => !current)}
        aria-expanded={expanded}
      >
        <span className="agentos-reasoning-title">
          <span aria-hidden="true" className="agentos-reasoning-indicator" />
          {label}
          {durationLabel ? (
            <span className="agentos-reasoning-duration">{durationLabel}</span>
          ) : null}
        </span>
        <span className="agentos-reasoning-state" aria-hidden="true">
          {expanded ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
        </span>
      </button>
      {expanded && step.content ? (
        <pre className="agentos-reasoning-content">{step.content}</pre>
      ) : null}
    </section>
  );
}
