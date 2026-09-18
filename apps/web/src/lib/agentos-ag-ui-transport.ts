import type { HttpAgentFetchFn } from "@ag-ui/client";

type FetchLike = typeof fetch;

type TransportOptions = {
  fetch?: FetchLike;
  onServerIds?: (threadId: string, runId: string) => void;
};

type TransportCallbacks = Pick<TransportOptions, "onServerIds">;

type PendingInterrupt = {
  id: string;
  tool_call_id: string;
};

const ACTIVE_RUN_STATUSES = new Set(["queued", "running"]);
const RECOVERY_DELAYS_MS = [1_000, 2_000, 5_000] as const;
const RECOVERY_MAX_ATTEMPTS = 60;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function errorDetail(response: Response, fallback: string): Promise<string> {
  const value: unknown = await response
    .clone()
    .json()
    .catch(() => null);
  return isRecord(value) && typeof value.detail === "string" ? value.detail : fallback;
}

function delay(delayMs: number, signal?: AbortSignal | null): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(signal.reason ?? new DOMException("操作已取消", "AbortError"));
      return;
    }
    const onAbort = () => {
      clearTimeout(timeout);
      reject(signal?.reason ?? new DOMException("操作已取消", "AbortError"));
    };
    const timeout = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, delayMs);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

/**
 * 仅翻译 AgentOS 的 BFF 契约；消息、工具和 interrupt 生命周期交给官方 runtime。
 */
export class AgentOsAgUiTransport {
  readonly #fetch: FetchLike;
  #callbacks: TransportCallbacks;
  readonly #interruptPayloads = new Map<string, Record<string, unknown>>();
  #revision = 0;

  serverRunId: string | null = null;
  serverThreadId: string | null = null;

  get revision(): number {
    return this.#revision;
  }

  constructor(options: TransportOptions = {}) {
    this.#fetch =
      options.fetch ??
      ((input, init) =>
        typeof window === "undefined" ? globalThis.fetch(input, init) : window.fetch(input, init));
    this.#callbacks = { onServerIds: options.onServerIds };
  }

  setCallbacks(callbacks: TransportCallbacks): void {
    this.#callbacks = callbacks;
  }

  setInterruptPayload(interruptId: string, payload: Record<string, unknown> | null): void {
    if (payload === null) {
      this.#interruptPayloads.delete(interruptId);
    } else {
      this.#interruptPayloads.set(interruptId, payload);
    }
  }

  restoreServerRun(
    runId: string | null,
    threadId: string | null,
    expectedRevision?: number,
  ): boolean {
    if (expectedRevision !== undefined && expectedRevision !== this.#revision) return false;
    if (this.serverRunId === runId && this.serverThreadId === threadId) return true;
    this.serverRunId = runId;
    this.serverThreadId = threadId;
    this.#revision += 1;
    return true;
  }

  async #waitForSettlement(runId: string, signal?: AbortSignal | null): Promise<void> {
    let lastError: unknown;
    for (let attempt = 0; attempt < RECOVERY_MAX_ATTEMPTS; attempt += 1) {
      try {
        const response = await this.#fetch(`/api/runs/${runId}`, {
          cache: "no-store",
          signal,
        });
        if (!response.ok) {
          throw new Error(await errorDetail(response, `无法恢复运行状态（${response.status}）`));
        }
        const state: unknown = await response.json();
        const status = isRecord(state) && typeof state.status === "string" ? state.status : null;
        if (status === null || !ACTIVE_RUN_STATUSES.has(status)) return;
      } catch (error) {
        if (signal?.aborted) throw error;
        lastError = error;
      }
      await delay(RECOVERY_DELAYS_MS[Math.min(attempt, 2)], signal);
    }
    throw lastError instanceof Error
      ? lastError
      : new Error("运行恢复等待超时，请稍后刷新会话查看结果。");
  }

  #recoverableStream(response: Response, runId: string, signal?: AbortSignal | null): Response {
    if (
      response.body === null ||
      !response.headers.get("content-type")?.includes("text/event-stream")
    ) {
      return response;
    }
    const reader = response.body.getReader();
    const fetchSettlement = () => this.#waitForSettlement(runId, signal);
    const body = new ReadableStream<Uint8Array>({
      async pull(controller) {
        try {
          const chunk = await reader.read();
          if (!chunk.done) {
            controller.enqueue(chunk.value);
            return;
          }
          await fetchSettlement();
          controller.close();
        } catch (error) {
          try {
            await fetchSettlement();
            controller.close();
          } catch (recoveryError) {
            controller.error(recoveryError ?? error);
          }
        }
      },
      cancel(reason) {
        void reader.cancel(reason);
      },
    });
    return new Response(body, {
      status: response.status,
      statusText: response.statusText,
      headers: response.headers,
    });
  }

  fetch: HttpAgentFetchFn = async (url, requestInit) => {
    this.#revision += 1;
    const input: unknown =
      typeof requestInit.body === "string" ? JSON.parse(requestInit.body) : null;
    const resume = isRecord(input) && Array.isArray(input.resume) ? input.resume : null;

    if (resume === null) {
      const response = await this.#fetch(
        url,
        this.serverThreadId !== null && isRecord(input)
          ? {
              ...requestInit,
              body: JSON.stringify({ ...input, threadId: this.serverThreadId }),
            }
          : requestInit,
      );
      const threadId = response.headers.get("X-AgentOS-Thread-ID");
      const runId = response.headers.get("X-AgentOS-Run-ID");
      if (threadId !== null && runId !== null) {
        this.restoreServerRun(runId, threadId);
        this.#callbacks.onServerIds?.(threadId, runId);
      }
      return runId === null
        ? response
        : this.#recoverableStream(response, runId, requestInit.signal);
    }

    const runId = this.serverRunId;
    if (runId === null) {
      throw new Error("缺少待恢复的服务端 Run 标识，请刷新会话后重试。");
    }

    const stateResponse = await this.#fetch(`/api/runs/${runId}`, {
      cache: "no-store",
      signal: requestInit.signal,
    });
    if (!stateResponse.ok) {
      throw new Error(await errorDetail(stateResponse, "无法读取待确认操作。"));
    }
    const state: unknown = await stateResponse.json();
    const pending =
      isRecord(state) && Array.isArray(state.pending_interrupts)
        ? state.pending_interrupts.filter(
            (item): item is PendingInterrupt =>
              isRecord(item) &&
              typeof item.id === "string" &&
              typeof item.tool_call_id === "string",
          )
        : [];
    const pendingById = new Map(pending.map((item) => [item.id, item]));

    const enrichedResume = resume.map((entry) => {
      if (!isRecord(entry) || typeof entry.interruptId !== "string") return entry;
      const extraPayload = this.#interruptPayloads.get(entry.interruptId);
      if (extraPayload === undefined) return entry;
      return {
        ...entry,
        payload: { ...(isRecord(entry.payload) ? entry.payload : {}), ...extraPayload },
      };
    });

    const decisions = enrichedResume.map((entry) => {
      if (!isRecord(entry) || typeof entry.interruptId !== "string") {
        throw new Error("确认响应格式无效。");
      }
      const interrupt = pendingById.get(entry.interruptId);
      if (interrupt === undefined) {
        throw new Error("待确认操作已变化，请刷新后重试。");
      }
      const payload = isRecord(entry.payload) ? entry.payload : {};
      const approved = entry.status === "resolved" && payload.approved === true;
      return {
        tool_call_id: interrupt.tool_call_id,
        decision: approved ? "approve" : "deny",
        message: typeof payload.reason === "string" ? payload.reason : null,
        override_args: isRecord(payload.override_args) ? payload.override_args : null,
      };
    });

    if (decisions.length !== pending.length) {
      throw new Error("必须一次处理全部待确认操作。");
    }

    requestInit.signal?.throwIfAborted();
    const resumeResponse = await this.#fetch(`/api/runs/${runId}/resume`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: requestInit.signal,
      body: JSON.stringify({
        idempotency_key: resume
          .map((entry) => (isRecord(entry) ? entry.interruptId : ""))
          .join(":"),
        decisions,
      }),
    });
    if (!resumeResponse.ok) {
      throw new Error(await errorDetail(resumeResponse, "确认提交失败，请稍后重试。"));
    }
    for (const entry of enrichedResume) {
      if (isRecord(entry) && typeof entry.interruptId === "string") {
        this.#interruptPayloads.delete(entry.interruptId);
      }
    }

    const streamResponse = await this.#fetch(`/api/runs/${runId}/stream`, {
      cache: "no-store",
      signal: requestInit.signal,
    });
    if (!streamResponse.ok || streamResponse.status === 204 || streamResponse.body === null) {
      await this.#waitForSettlement(runId, requestInit.signal);
      return new Response(null, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      });
    }
    return this.#recoverableStream(streamResponse, runId, requestInit.signal);
  };
}
