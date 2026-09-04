"use client";

/**
 * AgentSelector — pick which assistant (通用助手 / 遗传代谢 etc.) to chat with.
 *
 * Previously rendered inside ChatPanel's composer; restored as a standalone
 * control after the assistant-ui migration so the switch survives.
 */

import { Sparkles } from "lucide-react";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { displayAgentName, type AgentSummary } from "@/lib/agents";

type AgentSelectorProps = {
  agents: AgentSummary[];
  value: string | null;
  onChange: (agentId: string) => void;
  disabled?: boolean;
};

export function AgentSelector({ agents, value, onChange, disabled = false }: AgentSelectorProps) {
  return (
    <Select value={value ?? ""} onValueChange={onChange} disabled={agents.length === 0 || disabled}>
      <SelectTrigger
        aria-label="选择助手"
        className="h-7 w-auto max-w-56 gap-1.5 border-transparent bg-transparent px-1.5 text-xs font-semibold shadow-none hover:bg-accent focus-visible:ring-ring/40"
      >
        <Sparkles aria-hidden="true" className="size-3.5 text-[var(--accent)]" />
        <SelectValue placeholder="正在加载助手…" />
      </SelectTrigger>
      <SelectContent>
        {agents.map((agent) => (
          <SelectItem key={agent.id} value={agent.id}>
            {displayAgentName(agent.name)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
