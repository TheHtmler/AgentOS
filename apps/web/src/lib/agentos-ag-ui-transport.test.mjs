import assert from "node:assert/strict";
import test from "node:test";

const { AgentOsAgUiTransport } = await import("./agentos-ag-ui-transport.ts");

test("普通请求保存服务端分配的 Thread 与 Run 标识", async () => {
  const calls = [];
  const transport = new AgentOsAgUiTransport({
    fetch: async (url, init) => {
      calls.push({ url, init });
      return new Response("stream", {
        headers: {
          "X-AgentOS-Thread-ID": "thread-server",
          "X-AgentOS-Run-ID": "run-server",
        },
      });
    },
  });

  await transport.fetch("/api/ag-ui/runs", {
    method: "POST",
    body: JSON.stringify({ messages: [], runId: "browser-run" }),
  });

  assert.equal(transport.serverRunId, "run-server");
  assert.equal(transport.serverThreadId, "thread-server");
  assert.equal(calls.length, 1);
});

test("resume 将官方 interrupt 响应映射到同一服务端 Run", async () => {
  const calls = [];
  const transport = new AgentOsAgUiTransport({
    fetch: async (url, init) => {
      calls.push({ url, init });
      if (url === "/api/runs/run-server") {
        return Response.json({
          pending_interrupts: [
            {
              id: "interrupt-1",
              tool_call_id: "tool-1",
              tool_name: "case_slot_collect",
              tool_args: {},
              expires_at: "2026-09-18T00:00:00Z",
            },
          ],
        });
      }
      if (url === "/api/runs/run-server/resume") return Response.json({ status: "running" });
      if (url === "/api/runs/run-server/stream") return new Response("resume-stream");
      throw new Error(`意外请求：${url}`);
    },
  });
  transport.restoreServerRun("run-server", "thread-server");
  transport.setInterruptPayload("interrupt-1", {
    override_args: { values: { height_cm: "180" } },
  });

  const response = await transport.fetch("/api/ag-ui/runs", {
    method: "POST",
    body: JSON.stringify({
      resume: [
        {
          interruptId: "interrupt-1",
          status: "resolved",
          payload: { approved: true },
        },
      ],
    }),
  });

  assert.equal(await response.text(), "resume-stream");
  const resumeCall = calls.find((call) => call.url === "/api/runs/run-server/resume");
  assert.deepEqual(JSON.parse(resumeCall.init.body), {
    idempotency_key: "interrupt-1",
    decisions: [
      {
        tool_call_id: "tool-1",
        decision: "approve",
        message: null,
        override_args: { values: { height_cm: "180" } },
      },
    ],
  });
});

test("resume 事件 broker 不可用时回退到持久化 Run 状态", async () => {
  const calls = [];
  const transport = new AgentOsAgUiTransport({
    fetch: async (url, init) => {
      calls.push({ url, init });
      if (url === "/api/runs/run-server") {
        return Response.json({
          status:
            calls.filter((call) => call.url === url).length === 1
              ? "waiting_approval"
              : "completed",
          pending_interrupts: [{ id: "interrupt-1", tool_call_id: "tool-1" }],
        });
      }
      if (url === "/api/runs/run-server/resume") return Response.json({ status: "running" });
      if (url === "/api/runs/run-server/stream") return new Response(null, { status: 204 });
      throw new Error(`意外请求：${url}`);
    },
  });
  transport.restoreServerRun("run-server", "thread-server");

  const response = await transport.fetch("/api/ag-ui/runs", {
    method: "POST",
    body: JSON.stringify({
      resume: [{ interruptId: "interrupt-1", status: "resolved", payload: { approved: false } }],
    }),
  });

  assert.equal(response.status, 200);
  assert.equal(calls.filter((call) => call.url === "/api/runs/run-server").length, 2);
});

test("更新 callbacks 不会丢失 transport 内保存的服务端标识", async () => {
  const observed = [];
  const transport = new AgentOsAgUiTransport({
    fetch: async () =>
      new Response("stream", {
        headers: {
          "X-AgentOS-Thread-ID": "thread-server",
          "X-AgentOS-Run-ID": "run-server",
        },
      }),
    onServerIds: () => observed.push("old"),
  });
  transport.setCallbacks({ onServerIds: () => observed.push("new") });

  await transport.fetch("/api/ag-ui/runs", {
    method: "POST",
    body: JSON.stringify({ messages: [] }),
  });

  assert.deepEqual(observed, ["new"]);
  assert.equal(transport.serverRunId, "run-server");
});

test("旧历史刷新不能在下一轮请求开始后覆盖 Run 标识", async () => {
  const transport = new AgentOsAgUiTransport({
    fetch: async () => new Response("stream"),
  });
  transport.restoreServerRun("run-old", "thread-server");
  const oldRevision = transport.revision;

  const request = transport.fetch("/api/ag-ui/runs", {
    method: "POST",
    body: JSON.stringify({ messages: [] }),
  });

  assert.equal(transport.restoreServerRun("run-old", "thread-server", oldRevision), false);
  await request;
  assert.notEqual(transport.revision, oldRevision);
});
