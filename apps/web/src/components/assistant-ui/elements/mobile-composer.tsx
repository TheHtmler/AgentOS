"use client";

import type { ComponentProps } from "react";
import { ArrowUpIcon, MicIcon, PlusIcon, SquareIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { field, ghostButton, inkButton, mono } from "@/lib/surfaces";

export function MobileComposer({
  value,
  keyboardOpen,
  running,
  actions,
  onAction,
  onAttach,
  onValueChange,
  onSend,
  onStop,
  onFocus,
  className,
  ...props
}: Omit<
  ComponentProps<"div">,
  | "children"
  | "value"
  | "keyboardOpen"
  | "running"
  | "actions"
  | "onAction"
  | "onAttach"
  | "onValueChange"
  | "onSend"
  | "onStop"
  | "onFocus"
> & {
  value: string;
  keyboardOpen: boolean;
  running: boolean;
  actions: readonly string[];
  onAction?: (action: string) => void;
  onAttach?: () => void;
  onValueChange?: (value: string) => void;
  onSend?: () => void;
  onStop?: () => void;
  onFocus?: () => void;
}) {
  return (
    <div
      data-slot="mobile-composer"
      className={cn(
        "flex w-full max-w-[19rem] flex-col gap-2.5 rounded-t-[20px] border-t border-foreground/[0.07] bg-background px-3 pt-3",
        keyboardOpen ? "pb-3" : "pb-6",
        className,
      )}

      {...props}
    >
      {!keyboardOpen && (
        <div className="-mx-3 flex animate-in gap-1.5 overflow-x-auto px-3 pb-0.5 duration-200 fade-in">
          {actions.map((action) => (
            <button
              key={action}
              type="button"
              onClick={() => onAction?.(action)}
              disabled={!onAction}
              className={cn(
                "disabled:pointer-events-none",
                field,
                "shrink-0 rounded-full px-3 py-1.5 text-xs whitespace-nowrap text-foreground/60",
              )}
            >
              {action}
            </button>
          ))}
        </div>
      )}

      <div className="flex items-end gap-2">
        <button
          type="button"
          aria-label="Add an attachment"
          onClick={onAttach}
          disabled={!onAttach}
          className={cn(
            ghostButton,
            field,
            "size-9 shrink-0 disabled:pointer-events-none disabled:opacity-30",
          )}
        >
          <PlusIcon className="size-4" />
        </button>

        <div
          className={cn(field, "flex min-w-0 flex-1 items-center gap-2 rounded-[18px] px-3 py-2")}
        >
          <input
            value={value}
            onChange={(event) => onValueChange?.(event.target.value)}
            onFocus={onFocus}
            onKeyDown={(event) => {
              if (event.key !== "Enter" || event.shiftKey) return;
              if (event.nativeEvent.isComposing) return;
              event.preventDefault();
              if (!running && value !== "") onSend?.();
            }}
            placeholder="Message"
            aria-label="Message"
            className="min-w-0 flex-1 bg-transparent text-[16px] text-foreground/85 outline-none placeholder:text-foreground/30"
          />
          {value === "" && <MicIcon className="size-4 shrink-0 text-foreground/35" />}
        </div>

        <button
          type="button"
          aria-label={running ? "Stop" : "Send"}
          onClick={running ? onStop : onSend}
          disabled={!running && value === ""}
          className={cn(
            inkButton,
            "flex size-9 shrink-0 items-center justify-center rounded-full disabled:pointer-events-none disabled:opacity-25",
          )}
        >
          {running ? (
            <SquareIcon className="size-3 fill-current" />
          ) : (
            <ArrowUpIcon className="size-4" />
          )}
        </button>
      </div>

      {!keyboardOpen && (
        <span aria-hidden className="mx-auto h-1 w-28 rounded-full bg-foreground/15" />
      )}

      {keyboardOpen && (
        <span className={cn(mono, "text-center text-foreground/25")}>return to send</span>
      )}
    </div>
  );
}
