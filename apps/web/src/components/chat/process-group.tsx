"use client";

import { useState, type ReactNode } from "react";
import { ChevronRight, ListChecks } from "lucide-react";

import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";

/**
 * shadcn-style process group: thinking + tool rows live outside the reply
 * bubble. While the run is active the group stays expanded; on active→idle it
 * auto-collapses into a single "已完成 {duration}" row that can be reopened.
 */
export function ProcessGroup({
  isActive,
  durationLabel,
  stepCount,
  children,
}: {
  isActive: boolean;
  durationLabel?: string | null;
  stepCount: number;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(isActive);
  const [prevActive, setPrevActive] = useState(isActive);

  // Sync open/closed when the turn starts or finishes (React "adjust state while rendering").
  if (isActive !== prevActive) {
    setPrevActive(isActive);
    setOpen(isActive);
  }

  const title = isActive
    ? `正在执行${stepCount > 0 ? ` · ${stepCount} 步` : ""}`
    : durationLabel === "已中断"
      ? "已中断"
      : durationLabel
        ? `已完成 · ${stepCount} 步 · ${durationLabel}`
        : `已完成 · ${stepCount} 步`;

  return (
    <Collapsible
      open={open}
      onOpenChange={setOpen}
      className={cn(
        "overflow-hidden rounded-xl border border-border bg-card/70 backdrop-blur-sm transition-colors",
        isActive && "border-primary/30 bg-primary/[0.03]",
      )}
    >
      <CollapsibleTrigger className="flex w-full items-center justify-between gap-3 px-3.5 py-2 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring/50">
        <span className="flex min-w-0 items-center gap-2 text-[0.78rem] font-semibold text-foreground">
          {isActive ? (
            <span
              aria-hidden="true"
              className="size-1.5 shrink-0 animate-pulse rounded-full bg-primary"
            />
          ) : (
            <ListChecks aria-hidden="true" className="size-3.5 shrink-0 text-muted-foreground" />
          )}
          <span className="truncate">{title}</span>
        </span>
        <span className="inline-flex shrink-0 items-center gap-1 text-[0.7rem] font-medium text-muted-foreground">
          <ChevronRight
            aria-hidden="true"
            className={cn("size-3 transition-transform", open && "rotate-90")}
          />
          {open ? "收起详情" : "查看步骤"}
        </span>
      </CollapsibleTrigger>
      <CollapsibleContent className="grid gap-1.5 border-t border-border px-2.5 pt-1.5 pb-2.5">
        {children}
      </CollapsibleContent>
    </Collapsible>
  );
}
