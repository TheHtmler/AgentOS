"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

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

export default function SessionsPage() {
  const [threads, setThreads] = useState<OpsThread[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [draft, setDraft] = useState("");
  const [runStatus, setRunStatus] = useState<(typeof RUN_FILTERS)[number]>("all");
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

  return (
    <div className="stack">
      <div>
        <h1 className="page-title">会话</h1>
        <p className="muted page-lead">只读审计用户对话、Run 状态与待审批，不改写用户数据。</p>
      </div>

      <form
        className="filter-row"
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
      </form>

      <div className="filter-row">
        {RUN_FILTERS.map((item) => (
          <button
            key={item}
            type="button"
            className={`secondary ${runStatus === item ? "is-selected" : ""}`}
            onClick={() => setRunStatus(item)}
          >
            {item === "all" ? "全部状态" : RUN_STATUS_LABELS[item]}
          </button>
        ))}
        <label className="inline-check">
          <input
            type="checkbox"
            checked={includeDeleted}
            onChange={(event) => setIncludeDeleted(event.target.checked)}
          />
          含已删除
        </label>
      </div>

      {error ? <p className="error">{error}</p> : null}
      {loading ? <p className="muted">加载中…</p> : null}
      {!loading ? <p className="muted">共 {total} 条</p> : null}

      {!loading && threads.length > 0 ? (
        <div className="doc-list always">
          {threads.map((thread) => (
            <article key={thread.id} className="doc-card">
              <Link href={`/sessions/${thread.id}`} className="doc-card__title linkish">
                {displayTitle(thread.title)}
                {thread.deleted_at ? <span className="pill pill--danger">已删除</span> : null}
              </Link>
              <div className="muted" style={{ fontSize: "0.85rem" }}>
                {thread.user_email ?? "无账号"} · {thread.agent_name}
              </div>
              <div className="doc-card__meta">
                <span className={`badge badge--${thread.last_run_status ?? "unknown"}`}>
                  {labelOf(RUN_STATUS_LABELS, thread.last_run_status)}
                </span>
                <span>{thread.message_count} 条消息</span>
                <span>{formatTime(thread.updated_at)}</span>
              </div>
            </article>
          ))}
        </div>
      ) : null}

      {!loading && threads.length === 0 ? (
        <div className="panel">
          <p className="muted" style={{ margin: 0 }}>
            没有匹配的会话。
          </p>
        </div>
      ) : null}
    </div>
  );
}
