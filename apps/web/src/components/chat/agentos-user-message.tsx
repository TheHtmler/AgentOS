"use client";

import { ActionBarPrimitive, MessagePrimitive } from "@assistant-ui/react";
import { PencilIcon } from "lucide-react";

import { UserMessageAttachments } from "@/components/assistant-ui/elements/attachment.aui";
import { TooltipIconButton } from "@/components/assistant-ui/elements/tooltip-icon-button";
import { MessageTimestamp } from "@/components/chat/message-timestamp";

/** Product user-message slot with the durable message time under the bubble. */
export function AgentOsUserMessage() {
  return (
    <MessagePrimitive.Root
      data-slot="aui_user-message-root"
      data-role="user"
      className="grid animate-in auto-rows-auto grid-cols-[minmax(72px,1fr)_auto] content-start gap-y-1 px-2 duration-150 [&:where(>*)]:col-start-2"
    >
      <UserMessageAttachments />
      <div className="relative col-start-2 min-w-0">
        <div className="peer rounded-xl bg-muted px-4 py-2 wrap-break-word text-foreground">
          <MessagePrimitive.Parts />
        </div>
        <div className="absolute start-0 top-1/2 -translate-x-full -translate-y-1/2 pe-2 rtl:translate-x-full">
          <ActionBarPrimitive.Root hideWhenRunning autohide="not-last" className="flex flex-col">
            <ActionBarPrimitive.Edit asChild>
              <TooltipIconButton tooltip="编辑消息">
                <PencilIcon />
              </TooltipIconButton>
            </ActionBarPrimitive.Edit>
          </ActionBarPrimitive.Root>
        </div>
        <MessageTimestamp className="text-right" />
      </div>
    </MessagePrimitive.Root>
  );
}
