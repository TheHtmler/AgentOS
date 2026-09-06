"use client";

import {
  ActionBarPrimitive,
  AuiIf,
  ErrorPrimitive,
  MessagePrimitive,
  groupPartByType,
} from "@assistant-ui/react";
import { CheckIcon, CopyIcon, DownloadIcon } from "lucide-react";

import { File } from "@/components/assistant-ui/elements/file";
import { Image as MessageImage } from "@/components/assistant-ui/elements/image";
import { MarkdownText } from "@/components/assistant-ui/elements/markdown-text";
import { Reasoning } from "@/components/assistant-ui/elements/reasoning.aui";
import {
  ToolGroupContent,
  ToolGroupRoot,
  ToolGroupTrigger,
} from "@/components/assistant-ui/elements/tool-group.aui";
import { TooltipIconButton } from "@/components/assistant-ui/elements/tooltip-icon-button";
import { AgentOsToolFallback } from "@/components/chat/agentos-tool-fallback";

/** Product message slot: ordered parts and one action bar for the answer. */
export function AgentOsAssistantMessage() {
  return (
    <MessagePrimitive.Root
      data-slot="aui_assistant-message-root"
      data-role="assistant"
      className="min-w-0 px-2"
    >
      <div className="space-y-3 leading-relaxed wrap-break-word text-foreground">
        <MessagePrimitive.GroupedParts groupBy={groupPartByType({ "tool-call": ["group-tool"] })}>
          {({ part, children }) => {
            switch (part.type) {
              case "group-tool":
                return (
                  <ToolGroupRoot variant="ghost">
                    <ToolGroupTrigger
                      count={part.indices.length}
                      active={part.status.type === "running"}
                    />
                    <ToolGroupContent>{children}</ToolGroupContent>
                  </ToolGroupRoot>
                );
              case "text":
                return <MarkdownText />;
              case "reasoning":
                return <Reasoning {...part} />;
              case "tool-call":
                return part.toolUI ?? <AgentOsToolFallback {...part} />;
              case "image":
                return <MessageImage {...part} />;
              case "file":
                return <File {...part} />;
              case "data":
                return part.dataRendererUI;
              case "indicator":
                return (
                  <span className="animate-pulse" aria-label="正在生成">
                    ...
                  </span>
                );
              default:
                return children;
            }
          }}
        </MessagePrimitive.GroupedParts>
        <MessagePrimitive.Error>
          <ErrorPrimitive.Root className="rounded-md border border-destructive/30 p-3 text-sm text-destructive">
            <ErrorPrimitive.Message />
          </ErrorPrimitive.Root>
        </MessagePrimitive.Error>
      </div>
      <AuiIf
        condition={(s) =>
          s.message.content.some((part) => part.type === "text" && part.text.trim() !== "")
        }
      >
        <ActionBarPrimitive.Root
          hideWhenRunning
          autohide="not-last"
          className="mt-1.5 flex h-8 items-center gap-1 text-muted-foreground"
        >
          <ActionBarPrimitive.Copy asChild>
            <TooltipIconButton tooltip="复制回答">
              <AuiIf condition={(s) => s.message.isCopied}>
                <CheckIcon />
              </AuiIf>
              <AuiIf condition={(s) => !s.message.isCopied}>
                <CopyIcon />
              </AuiIf>
            </TooltipIconButton>
          </ActionBarPrimitive.Copy>
          <ActionBarPrimitive.ExportMarkdown asChild>
            <TooltipIconButton tooltip="导出 Markdown">
              <DownloadIcon />
            </TooltipIconButton>
          </ActionBarPrimitive.ExportMarkdown>
        </ActionBarPrimitive.Root>
      </AuiIf>
    </MessagePrimitive.Root>
  );
}
