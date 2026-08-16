"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { displayTitle, formatTime } from "@/lib/format";
import { MESSAGE_ROLE_LABELS, RUN_STATUS_LABELS, USER_STATUS_LABELS, labelOf } from "@/lib/labels";
import { opsJson } from "@/lib/ops-fetch";

type OpsMessage = {
  id: string;
  seq: number;
  role: string;
  content: string;
  created_at: string;
  truncated: boolean;
};

type OpsRun = {
  id: string;
  status: string;
  model_name: string;
  error_message: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
};

type OpsThreadDetail = {
  id: string;
  title: string | null;
  user_email: string | null;
  user_status: string | null;
  agent_name: string;
  agent_slug: string;
  case_id: string | null;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
  last_run_status: string | null;
  message_count: number;
  messages: OpsMessage[];
  runs: OpsRun[];
};

export default function SessionDetailPage() {
  const params = useParams<{ threadId: string }>();
  const [detail, setDetail] = useState<OpsThreadDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setDetail(await opsJson<OpsThreadDetail>(`/api/ops/sessions/${params.threadId}`));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    }
  }, [params.threadId]);

  useEffect(() => {
    void (async () => {
      await load();
    })();
  }, [load]);

  if (!detail && !error) {
    return <p className="muted">加载中…</p>;
  }

  return (
    <div className="stack">
      <div>
        <Link href="/sessions" className="muted">
          ← 返回会话
        </Link>
        <h1 className="page-title">{displayTitle(detail?.title)}</h1>
        <p className="muted">只读审计 · 正文超过 2000 字会截断</p>
      </div>

      {error ? <p className="error">{error}</p> : null}

      {detail ? (
        <>
          <section className="panel stack">
            <h2 className="section-title">会话信息</h2>
            <div className="meta-grid">
              <div>
                <span className="muted">用户</span>
                <strong>{detail.user_email ?? "无账号"}</strong>
              </div>
              <div>
                <span className="muted">用户状态</span>
                <strong>{labelOf(USER_STATUS_LABELS, detail.user_status)}</strong>
              </div>
              <div>
                <span className="muted">智能体</span>
                <strong>
                  {detail.agent_name} ({detail.agent_slug})
                </strong>
              </div>
              <div>
                <span className="muted">最近 Run</span>
                <strong>{labelOf(RUN_STATUS_LABELS, detail.last_run_status)}</strong>
              </div>
              <div>
                <span className="muted">消息数</span>
                <strong>{detail.message_count}</strong>
              </div>
              <div>
                <span className="muted">更新时间</span>
                <strong>{formatTime(detail.updated_at)}</strong>
              </div>
            </div>
            {detail.deleted_at ? (
              <p className="error">该会话已于 {formatTime(detail.deleted_at)} 软删除</p>
            ) : null}
            {detail.case_id ? <p className="muted">档案 ID：{detail.case_id}</p> : null}
          </section>

          <section className="panel stack">
            <h2 className="section-title">最近 Run</h2>
            {detail.runs.length === 0 ? <p className="muted">暂无 Run</p> : null}
            {detail.runs.map((run) => (
              <article key={run.id} className="doc-card">
                <div className="doc-card__title">
                  <span className={`badge badge--${run.status}`}>
                    {labelOf(RUN_STATUS_LABELS, run.status)}
                  </span>
                  <span style={{ marginLeft: 8 }}>{run.model_name}</span>
                </div>
                <div className="doc-card__meta">
                  <span>开始 {formatTime(run.started_at ?? run.created_at)}</span>
                  <span>结束 {formatTime(run.completed_at)}</span>
                  <span>
                    tokens {run.input_tokens ?? "—"} / {run.output_tokens ?? "—"}
                  </span>
                </div>
                {run.error_message ? <pre className="chunk-body">{run.error_message}</pre> : null}
              </article>
            ))}
          </section>

          <section className="panel stack">
            <h2 className="section-title">最近消息</h2>
            {detail.messages.length === 0 ? <p className="muted">暂无消息</p> : null}
            {detail.messages.map((message) => (
              <article key={message.id} className="doc-card">
                <div className="doc-card__meta">
                  <span>#{message.seq}</span>
                  <span>{labelOf(MESSAGE_ROLE_LABELS, message.role)}</span>
                  <span>{formatTime(message.created_at)}</span>
                  {message.truncated ? <span className="pill">已截断</span> : null}
                </div>
                <pre className="chunk-body">{message.content}</pre>
              </article>
            ))}
          </section>
        </>
      ) : null}
    </div>
  );
}
