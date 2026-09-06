"use client";

import { ComposerPrimitive, useAui, useAuiState } from "@assistant-ui/react";
import { MicIcon, SquareIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ComposerAttachments } from "@/components/assistant-ui/elements/attachment.aui";
import { MobileComposer } from "@/components/assistant-ui/elements/mobile-composer";
import { TooltipIconButton } from "@/components/assistant-ui/elements/tooltip-icon-button";

const QUICK_ACTIONS = ["总结当前对话", "梳理待办", "继续分析"] as const;

/** Mobile layout for the existing assistant-ui runtime and AgentOS upload adapter. */
export function AgentOsMobileComposer() {
  const aui = useAui();
  const inputRef = useRef<HTMLInputElement>(null);
  const [keyboardOpen, setKeyboardOpen] = useState(false);
  const text = useAuiState((state) => state.composer.text);
  const isRunning = useAuiState((state) => state.thread.isRunning);
  const accepts = useAuiState((state) => state.composer.attachmentAccept);
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

  const addFiles = (files: FileList | null) => {
    if (files === null) return;
    for (const file of Array.from(files)) {
      void aui.composer().addAttachment(file);
    }
  };

  return (
    <div className="aui-agentos-mobile-composer md:hidden">
      <ComposerAttachments />
      <div className="relative">
        <MobileComposer
          value={text}
          keyboardOpen={keyboardOpen}
          running={isRunning}
          actions={QUICK_ACTIONS}
          onAction={(action) => aui.composer().setText(action)}
          onAttach={() => inputRef.current?.click()}
          onValueChange={(value) => aui.composer().setText(value)}
          onSend={() => aui.composer().send()}
          onStop={() => aui.composer().cancel()}
          onFocus={() => setKeyboardOpen(true)}
          className="max-w-none rounded-t-2xl"
        />
        <input
          ref={inputRef}
          type="file"
          accept={accepts}
          multiple
          className="sr-only"
          onChange={(event) => {
            addFiles(event.target.files);
            event.target.value = "";
          }}
        />
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
    </div>
  );
}
