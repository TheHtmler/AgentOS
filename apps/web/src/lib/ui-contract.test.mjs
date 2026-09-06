import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

function source(relativePath) {
  return readFileSync(new URL(`../${relativePath}`, import.meta.url), "utf8");
}

test("interactive primitives expose accessible tooltip and bounded scrolling behavior", () => {
  const tooltip = source("components/ui/tooltip.tsx");
  assert.match(tooltip, /@radix-ui\/react-tooltip/);
  assert.match(tooltip, /TooltipPrimitive\.Content/);
  assert.match(source("components/ui/scroll-area.tsx"), /overscroll-contain/);
});

test("workspace mobile navigation uses the shared Sheet primitive", () => {
  const workspace = source("components/chat/chat-workspace.tsx");
  assert.match(workspace, /@\/components\/ui\/sheet/);
  assert.doesNotMatch(workspace, /agentos-mobile-menu-backdrop/);
});

test("conversation actions use the shared DropdownMenu primitive", () => {
  const conversations = source("components/chat/conversation-list.tsx");
  assert.match(conversations, /@\/components\/ui\/dropdown-menu/);
  assert.doesNotMatch(conversations, /agentos-conversation-menu-button/);
});

test("append-only chat does not advertise unsupported editing or regeneration", () => {
  const thread = source("components/chat/assistant-thread.tsx");
  assert.match(thread, /onNew:\s*agui\.onNew/);
  assert.doesNotMatch(thread, /onEdit:|onReload:/);
});
