"use client";

import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

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
      className={cn(
        "group flex flex-col overflow-hidden rounded-lg border bg-card/70 backdrop-blur-sm transition-colors",
        running && "border-primary/30 bg-primary/[0.03]",
      )}
      aria-live="polite"
    >
      <button
        type="button"
        className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
        onClick={() => setExpanded((current) => !current)}
        aria-expanded={expanded}
      >
        <span className="flex min-w-0 items-center gap-2 text-[0.78rem] font-semibold text-foreground">
          <span
            aria-hidden="true"
            className={cn(
              "size-1.5 shrink-0 rounded-full",
              running ? "animate-pulse bg-primary" : "bg-muted-foreground/50",
            )}
          />
          <span className="truncate">{label}</span>
          {durationLabel ? (
            <Badge variant="secondary" className="px-1.5 py-0 text-[0.65rem] font-medium">
              {durationLabel}
            </Badge>
          ) : null}
        </span>
        <span
          aria-hidden="true"
          className="inline-flex shrink-0 items-center text-muted-foreground"
        >
          {expanded ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
        </span>
      </button>
      {expanded && step.content ? (
        <pre className="max-h-64 overflow-y-auto border-t border-border bg-muted/30 px-3 py-2.5 font-mono text-xs leading-relaxed whitespace-pre-wrap text-muted-foreground [scrollbar-width:thin]">
          {step.content}
        </pre>
      ) : null}
    </section>
  );
}
