"use client";

import { useEffect, useLayoutEffect, useRef } from "react";

export type ThinkingStepState = {
  kind: "thinking";
  id: string;
  content: string;
  status: "running" | "done";
  expanded: boolean;
  afterMessageId: string;
};

/**
 * Compact thinking strip (~3 lines) while running. No ordinal — multiple thinking
 * segments all read as "思考". Nested scroll must not affect session follow UI.
 */
export function ThinkingStepCard({ step }: { step: ThinkingStepState; onToggle?: () => void }) {
  const previewRef = useRef<HTMLPreElement | null>(null);
  const running = step.status === "running";
  const label = running ? "思考…" : "思考";

  useLayoutEffect(() => {
    const node = previewRef.current;
    if (!running || node === null) {
      return;
    }
    node.scrollTop = node.scrollHeight;
  }, [running, step.content]);

  useEffect(() => {
    const node = previewRef.current;
    if (node === null) {
      return;
    }

    const onWheel = (event: WheelEvent) => {
      const overflow = node.scrollHeight > node.clientHeight + 1;
      if (!overflow) {
        return;
      }
      const atTop = node.scrollTop <= 0;
      const atBottom = node.scrollHeight - node.scrollTop - node.clientHeight <= 1;
      const scrollingUp = event.deltaY < 0;
      const scrollingDown = event.deltaY > 0;
      if ((scrollingUp && !atTop) || (scrollingDown && !atBottom)) {
        // Keep the gesture inside thinking; do not notify session auto-scroll.
        event.preventDefault();
        event.stopPropagation();
        node.scrollTop += event.deltaY;
      } else {
        event.stopPropagation();
      }
    };

    node.addEventListener("wheel", onWheel, { passive: false });
    return () => node.removeEventListener("wheel", onWheel);
  }, [running, step.content]);

  return (
    <section className={`agentos-reasoning ${running ? "agentos-reasoning-running" : ""}`}>
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
