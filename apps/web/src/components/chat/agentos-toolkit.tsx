"use client";

import { defineToolkit } from "@assistant-ui/react";

import { AgentPlan } from "@/components/assistant-ui/elements/agent-plan";
import { AgentOsToolFallback } from "@/components/chat/agentos-tool-fallback";

function parsePlan(args: Record<string, unknown>) {
  const active = args.activeIndex ?? args.active_index;
  if (!Array.isArray(args.steps) || !args.steps.every((step) => typeof step === "string")) {
    return null;
  }
  if (typeof active !== "number" || !Number.isFinite(active)) return null;
  return { steps: args.steps, activeIndex: active };
}

/** 后端工具只注册展示能力；工具定义与执行仍以 AgentOS 服务端为准。 */
export const agentOsToolkit = defineToolkit({
  update_plan: {
    type: "backend" as const,
    render: (props) => {
      const plan = parsePlan(props.args);
      return plan === null ? <AgentOsToolFallback {...props} /> : <AgentPlan {...plan} />;
    },
  },
});
