"use client";

import { useAuiState } from "@assistant-ui/react";

import { formatMessageTimestamp } from "@/lib/format-time";

/** Shared quiet timestamp for both assistant and user message slots. */
export function MessageTimestamp({ className = "" }: { className?: string }) {
  const createdAt = useAuiState((state) => state.message.createdAt);
  const label = formatMessageTimestamp(createdAt.toISOString());

  if (!label) return null;

  return (
    <time
      dateTime={createdAt.toISOString()}
      className={`block text-[11px] leading-4 text-muted-foreground/70 ${className}`}
    >
      {label}
    </time>
  );
}
