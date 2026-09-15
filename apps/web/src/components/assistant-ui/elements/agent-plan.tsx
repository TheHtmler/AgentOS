"use client";

import type { ComponentProps } from "react";
import { CheckIcon, Loader2Icon } from "lucide-react";
import { cn } from "@/lib/utils";
import { mono } from "./surfaces";
import { pct, progressOf } from "../utils/range";

export function AgentPlan({
  steps,
  activeIndex,
  className,
  ...props
}: Omit<ComponentProps<"div">, "children" | "steps" | "activeIndex"> & {
  steps: readonly string[];
  activeIndex: number;
}) {
  const total = steps.length;
  const completed = progressOf(activeIndex, total);
  const allDone = completed >= total;
  const progress = pct(completed, total);

  return (
    <div
      data-slot="agent-plan"
      className={cn("flex w-full max-w-sm flex-col gap-3", className)}

      {...props}
    >
      <div className="flex items-center justify-between">
        <span className="text-[13.5px] font-medium">执行计划</span>
        <span className={cn(mono, "text-foreground/35 tabular-nums")}>
          {completed} / {total}
        </span>
      </div>
      <div className="h-[3px] w-full overflow-hidden rounded-full bg-foreground/[0.06]">
        <span
          className="block h-full rounded-full bg-foreground/80 transition-[width] duration-500"
          style={{ width: `${progress}%` }}
        />
      </div>
      <ul className="flex flex-col gap-2.5">
        {steps.map((step, i) => {
          const done = allDone || i < completed;
          const active = !allDone && i === completed;
          return (
            <li key={step} className="flex items-center gap-2.5 text-[13.5px]">
              <span className="flex size-4 shrink-0 items-center justify-center">
                {done ? (
                  <CheckIcon className="size-3.5 text-foreground/35" />
                ) : active ? (
                  <Loader2Icon className="size-3.5 animate-spin text-foreground/90 motion-reduce:animate-none" />
                ) : (
                  <span aria-hidden className="size-1.5 rounded-full bg-foreground/15" />
                )}
              </span>
              <span
                className={cn(
                  done && "text-foreground/40",
                  active && "text-foreground/90",
                  !done && !active && "text-foreground/35",
                )}
              >
                {step}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
