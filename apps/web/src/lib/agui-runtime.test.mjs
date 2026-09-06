import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import test from "node:test";

const hooks = registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith("@/lib/")) {
      return nextResolve(new URL(`./${specifier.slice(6)}.ts`, import.meta.url).href, context);
    }
    return nextResolve(specifier, context);
  },
});
const { convertAguiMessages, historyToDisplayMessages } = await import("./agui-runtime.ts");
hooks.deregister();

test("history places ordered tool summaries before their own final answer", () => {
  const history = {
    messages: [
      { id: "u1", role: "user", content: "first", attachments: [] },
      { id: "a1", role: "assistant", content: "first answer", attachments: [] },
      { id: "u2", role: "user", content: "second", attachments: [] },
      { id: "a2", role: "assistant", content: "second answer", attachments: [] },
      { id: "u3", role: "user", content: "waiting", attachments: [] },
    ],
    tool_calls: [
      { id: "t1", tool_name: "search", args: {}, status: "done", after_message_id: "u1" },
      { id: "t2", tool_name: "fetch", args: {}, status: "done", after_message_id: "u1" },
      { id: "t3", tool_name: "search", args: {}, status: "error", after_message_id: "u3" },
    ],
  };
  const messages = convertAguiMessages(historyToDisplayMessages(history));
  assert.deepEqual(
    messages.map((m) => m.content.map((p) => p.type)),
    [["text"], ["tool-call", "tool-call"], ["text"], ["text"], ["text"], ["text"], ["tool-call"]],
  );
  assert.deepEqual(
    messages[1].content.map((p) => p.toolCallId),
    ["t1", "t2"],
  );
  assert.equal(messages[2].content[0].text, "first answer");
  assert.equal(messages[6].content[0].isError, true);
});

test("stream parts preserve reasoning, text and tool order without empty text separators", () => {
  const messages = convertAguiMessages([
    { id: "u", role: "user", content: "question" },
    { id: "r", role: "reasoning", content: "reasoning" },
    { id: "a", role: "assistant", content: "Checking sources" },
    {
      id: "t",
      role: "assistant",
      content: "",
      toolCalls: [{ id: "call", type: "function", function: { name: "search", arguments: "{}" } }],
    },
    { id: "result", role: "tool", toolCallId: "call", content: "found" },
    { id: "final", role: "assistant", content: "answer" },
  ]);
  assert.deepEqual(
    messages.flatMap((m) => m.content.map((p) => p.type)),
    ["text", "reasoning", "text", "tool-call", "text"],
  );
  assert.equal(messages[3].content[0].result, "found");
});
