"use client";

import { ComposerPrimitive, useAui, useAuiState } from "@assistant-ui/react";
import { ArrowUpIcon, MicIcon, PlusIcon, SquareIcon } from "lucide-react";
import { useEffect, useState } from "react";

import { ComposerAttachments } from "@/components/assistant-ui/elements/attachment.aui";
import { TooltipIconButton } from "@/components/assistant-ui/elements/tooltip-icon-button";
import { cn } from "@/lib/utils";

const QUICK_ACTIONS = ["总结当前对话", "梳理待办", "继续分析"] as const;

/** Mobile layout for the existing assistant-ui runtime and AgentOS upload adapter. */
export function AgentOsMobileComposer() {
  const aui = useAui();
  const [keyboardOpen, setKeyboardOpen] = useState(false);
  const isRunning = useAuiState((state) => state.thread.isRunning);
  const canDictate = useAuiState((state) => state.thread.capabilities.dictation);
  const dictation = useAuiState((state) => state.composer.dictation);

  useEffect(() => {
    const viewport = window.visualViewport;
    if (viewport === null) return undefined;

    const updateKeyboardState = () => {
      setKeyboardOpen(window.innerHeight - viewport.height > 160);
    };

    updateKeyboardState();
    viewport.addEventListener("resize", updateKeyboardState);
    return () => viewport.removeEventListener("resize", updateKeyboardState);
  }, []);

  return (
    <ComposerPrimitive.Root className="aui-agentos-mobile-composer md:hidden">
      <ComposerAttachments />
      <div
        data-slot="mobile-composer"
        className={cn(
          "flex w-full flex-col gap-2.5 rounded-t-[20px] border-t border-foreground/[0.07] bg-background px-3 pt-3",
          keyboardOpen ? "pb-3" : "pb-6",
        )}
      >
        {!keyboardOpen && (
          <div className="-mx-3 flex animate-in gap-1.5 overflow-x-auto px-3 pb-0.5 duration-200 fade-in">
            {QUICK_ACTIONS.map((action) => (
              <button
                key={action}
                type="button"
                onClick={() => aui.composer().setText(action)}
                className="shrink-0 rounded-full bg-foreground/[0.04] px-3 py-1.5 text-xs whitespace-nowrap text-foreground/60"
              >
                {action}
              </button>
            ))}
          </div>
        )}

        <div className="flex items-end gap-2">
          <ComposerPrimitive.AddAttachment asChild>
            <TooltipIconButton
              tooltip="添加附件"
              type="button"
              variant="ghost"
              size="icon"
              className="size-9 shrink-0 rounded-full bg-foreground/[0.04] text-foreground/60"
              aria-label="添加附件"
            >
              <PlusIcon className="size-4" />
            </TooltipIconButton>
          </ComposerPrimitive.AddAttachment>

          <div className="flex min-w-0 flex-1 items-center gap-2 rounded-[18px] bg-foreground/[0.04] px-3 py-2">
            <ComposerPrimitive.Input
              placeholder="输入消息"
              aria-label="消息输入"
              rows={1}
              autoFocus={false}
              onFocus={() => setKeyboardOpen(true)}
              className="min-h-6 min-w-0 flex-1 resize-none bg-transparent text-base leading-6 text-foreground/85 outline-none placeholder:text-foreground/30"
            />
            {!isRunning ? <MicIcon className="size-4 shrink-0 text-foreground/35" /> : null}
          </div>

          {!isRunning ? (
            <ComposerPrimitive.Send asChild>
              <TooltipIconButton
                tooltip="发送消息"
                type="button"
                variant="default"
                size="icon"
                className="size-9 shrink-0 rounded-full"
                aria-label="发送消息"
              >
                <ArrowUpIcon className="size-4" />
              </TooltipIconButton>
            </ComposerPrimitive.Send>
          ) : (
            <ComposerPrimitive.Cancel asChild>
              <TooltipIconButton
                tooltip="停止生成"
                type="button"
                variant="default"
                size="icon"
                className="size-9 shrink-0 rounded-full"
                aria-label="停止生成"
              >
                <SquareIcon className="size-3 fill-current" />
              </TooltipIconButton>
            </ComposerPrimitive.Cancel>
          )}
        </div>

        {!keyboardOpen ? (
          <span aria-hidden className="mx-auto h-1 w-28 rounded-full bg-foreground/15" />
        ) : null}
        {keyboardOpen ? (
          <span className="text-center font-mono text-[11px] text-foreground/25">回车发送</span>
        ) : null}

        {canDictate ? (
          <div className="absolute right-14 bottom-6 flex size-9 items-center justify-center">
            {dictation === undefined ? (
              <ComposerPrimitive.Dictate asChild>
                <TooltipIconButton
                  tooltip="语音输入"
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="size-9 text-muted-foreground"
                  aria-label="开始语音输入"
                >
                  <MicIcon className="size-4" />
                </TooltipIconButton>
              </ComposerPrimitive.Dictate>
            ) : (
              <ComposerPrimitive.StopDictation asChild>
                <TooltipIconButton
                  tooltip="停止语音输入"
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="size-9 text-destructive"
                  aria-label="停止语音输入"
                >
                  <SquareIcon className="size-3.5 fill-current" />
                </TooltipIconButton>
              </ComposerPrimitive.StopDictation>
            )}
          </div>
        ) : null}
      </div>
    </ComposerPrimitive.Root>
  );
}
