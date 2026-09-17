"use client";

import { createContext, useContext } from "react";

type InterruptPayloadContextValue = {
  setInterruptPayload: (interruptId: string, payload: Record<string, unknown> | null) => void;
};

const InterruptPayloadContext = createContext<InterruptPayloadContextValue | null>(null);

export const AgentOsInterruptPayloadProvider = InterruptPayloadContext.Provider;

export function useAgentOsInterruptPayloads(): InterruptPayloadContextValue {
  const value = useContext(InterruptPayloadContext);
  if (value === null) {
    throw new Error("病例审批必须位于 AgentOS interrupt payload provider 内。");
  }
  return value;
}
