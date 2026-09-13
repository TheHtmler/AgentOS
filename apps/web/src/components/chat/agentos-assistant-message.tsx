"use client";

import {
  ActionBarPrimitive,
  AuiIf,
  ErrorPrimitive,
  MessagePrimitive,
  useAuiState,
  groupPartByType,
} from "@assistant-ui/react";
import { CheckIcon, CopyIcon, DownloadIcon, SearchIcon, TerminalIcon } from "lucide-react";
import { useState } from "react";

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
import { MessageTimestamp } from "@/components/chat/message-timestamp";
import { ToolTimeline, type TimelineStep } from "@/components/assistant-ui/elements/tool-timeline";

const TOOL_VERBS: Record<string, { verb: string; icon: typeof SearchIcon }> = {
  web_search: { verb: "搜索", icon: SearchIcon },
  fetch_url: { verb: "抓取", icon: SearchIcon },
  knowledge_search: { verb: "检索知识", icon: SearchIcon },
  read_artifact: { verb: "读取文件", icon: SearchIcon },
  sandbox_exec: { verb: "运行代码", icon: TerminalIcon },
};

function AssistantToolTimeline() {
  const toolCalls = useAuiState((state) =>
    state.message.parts.filter((part) => part.type === "tool-call"),
  );
  const streaming = useAuiState((state) => state.message.status?.type === "running");
  const [open, setOpen] = useState(false);

  if (toolCalls.length === 0) return null;

  const steps: TimelineStep[] = toolCalls.map((part) => {
    const meta = TOOL_VERBS[part.toolName] ?? { verb: part.toolName, icon: TerminalIcon };
    const args = part.args && typeof part.args === "object" ? part.args : {};
    const record = args as Record<string, unknown>;
    const chip = String(record.query ?? record.url ?? record.path ?? part.toolCallId);
    return { verb: meta.verb, chip: chip.slice(0, 64), icon: meta.icon };
  });

  return (
    <ToolTimeline
      steps={steps}
      visibleSteps={steps.length}
      streaming={streaming}
      open={open}
      onOpenChange={setOpen}
      restingLabel={`${steps.length} 个步骤`}
      activeLabel="工作中"
      stats={[]}
    />
  );
}

/** Product message slot: ordered parts and one action bar for the answer. */
export function AgentOsAssistantMessage() {
  return (
    <MessagePrimitive.Root
      data-slot="aui_assistant-message-root"
      data-role="assistant"
      className="min-w-0 px-2"
    >
      <AssistantToolTimeline />
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
      <MessageTimestamp className="px-2" />
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
