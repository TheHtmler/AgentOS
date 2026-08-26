import assert from "node:assert/strict";
import test from "node:test";

import * as agentModule from "./agents.ts";

const GENERAL_AGENT_ID = "00000000-0000-0000-0000-000000000001";

function agent(overrides) {
  return {
    id: "00000000-0000-0000-0000-000000000002",
    slug: "imd",
    name: "遗传代谢",
    description: null,
    kind: "vertical",
    is_default: false,
    memory_enabled: true,
    case_enabled: true,
    supports_vision: true,
    ...overrides,
  };
}

test("首次加载时默认选择 general，并在列表返回后校准其 ID", () => {
  const initialAgentId = agentModule.resolveSelectedAgentId?.(null, []);
  assert.equal(initialAgentId, GENERAL_AGENT_ID);

  const apiGeneralAgentId = "00000000-0000-0000-0000-000000000009";
  const agents = [
    agent({ is_default: true }),
    agent({ id: apiGeneralAgentId, slug: "general", name: "General", kind: "general" }),
  ];
  assert.equal(agentModule.resolveSelectedAgentId?.(initialAgentId, agents), apiGeneralAgentId);
});

test("已选择且仍有效的 Agent 不会被默认值覆盖", () => {
  const selectedAgentId = "00000000-0000-0000-0000-000000000002";
  const agents = [
    agent({ id: GENERAL_AGENT_ID, slug: "general", name: "General", kind: "general" }),
    agent({ id: selectedAgentId }),
  ];

  assert.equal(agentModule.resolveSelectedAgentId?.(selectedAgentId, agents), selectedAgentId);
});
