"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";

import { PageHeader } from "@/components/page-header";
import { Skeleton } from "@/components/skeleton";
import { displayTitle, formatTime } from "@/lib/format";
import { RUN_STATUS_LABELS, labelOf } from "@/lib/labels";
import { opsJson } from "@/lib/ops-fetch";

type OpsThread = {
  id: string;
  title: string | null;
  user_email: string | null;
  agent_name: string;
  agent_slug: string;
  updated_at: string;
  deleted_at: string | null;
  last_run_status: string | null;
  message_count: number;
};

const RUN_FILTERS = ["all", ...Object.keys(RUN_STATUS_LABELS)] as const;

function SessionsBody() {
  const searchParams = useSearchParams();
  const initialStatus = searchParams.get("run_status");
  const [threads, setThreads] = useState<OpsThread[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [draft, setDraft] = useState("");
  const [runStatus, setRunStatus] = useState<(typeof RUN_FILTERS)[number]>(
    initialStatus && initialStatus in RUN_STATUS_LABELS
      ? (initialStatus as (typeof RUN_FILTERS)[number])
      : "all",
  );
  const [includeDeleted, setIncludeDeleted] = useState(false);

  const load = useCallback(async () => {
    const params = new URLSearchParams();
    if (query.trim()) params.set("q", query.trim());
    if (runStatus !== "all") params.set("run_status", runStatus);
    if (includeDeleted) params.set("include_deleted", "true");
    try {
      const body = await opsJson<{ threads: OpsThread[]; total: number }>(
        `/api/ops/sessions?${params.toString()}`,
      );
      setThreads(body.threads);
      setTotal(body.total);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [includeDeleted, query, runStatus]);

  useEffect(() => {
    void (async () => {
      await load();
    })();
  }, [load]);

  useEffect(() => {
    const params = new URLSearchParams(searchParams.toString());
    if (runStatus === "all") params.delete("run_status");
    else params.set("run_status", runStatus);
    const next = params.toString();
    const current = searchParams.toString();
    if (next !== current) {
      window.history.replaceState(null, "", next ? `/sessions?${next}` : "/sessions");
    }
  }, [runStatus, searchParams]);

  return (
    <div className="stack">
      <PageHeader title="会话" lead="只读审计对话和 Run，不改写用户数据。" />

      <form
        className="toolbar"
        onSubmit={(event) => {
          event.preventDefault();
          setQuery(draft);
        }}
      >
        <input
          value={draft}
          placeholder="搜索标题、邮箱、智能体或会话 ID"
          onChange={(event) => setDraft(event.target.value)}
          className="search-input"
        />
        <button type="submit" className="secondary">
          搜索
        </button>
        <label className="inline-check">
          <input
            type="checkbox"
            checked={includeDeleted}
            onChange={(event) => setIncludeDeleted(event.target.checked)}
          />
          含已删除
        </label>
      </form>

      <div className="seg" role="tablist" aria-label="Run 状态">
        {RUN_FILTERS.map((item) => (
          <button
            key={item}
            type="button"
            className={runStatus === item ? "is-selected" : ""}
            onClick={() => setRunStatus(item)}
          >
            {item === "all" ? "全部" : RUN_STATUS_LABELS[item]}
          </button>
        ))}
      </div>

      {error ? <p className="error">{error}</p> : null}
      {loading ? <Skeleton /> : <p className="muted">{total} 条结果</p>}

      {!loading && threads.length > 0 ? (
        <div className="row-list">
          {threads.map((thread) => (
            <Link key={thread.id} href={`/sessions/${thread.id}`} className="row">
              <div>
                <div className="row__title">
                  {displayTitle(thread.title)}
                  {thread.deleted_at ? <span className="pill pill--danger">已删除</span> : null}
                </div>
                <div className="row__meta">
                  <span>{thread.user_email ?? "无账号"}</span>
                  <span>{thread.agent_name}</span>
                  <span>{thread.message_count} 条消息</span>
                </div>
              </div>
              <span className={`badge badge--${thread.last_run_status ?? "unknown"}`}>
                {labelOf(RUN_STATUS_LABELS, thread.last_run_status)}
              </span>
              <span className="muted">{formatTime(thread.updated_at)}</span>
            </Link>
          ))}
        </div>
      ) : null}

      {!loading && threads.length === 0 ? <div className="empty">没有匹配的会话。</div> : null}
    </div>
  );
}

export default function SessionsPage() {
  return (
    <Suspense fallback={<Skeleton />}>
      <SessionsBody />
    </Suspense>
  );
}
