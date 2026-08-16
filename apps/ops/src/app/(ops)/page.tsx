"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { displayTitle, formatTime } from "@/lib/format";
import { RUN_STATUS_LABELS, labelOf } from "@/lib/labels";
import { opsJson } from "@/lib/ops-fetch";

type RecentThread = {
  id: string;
  title: string | null;
  user_email: string | null;
  agent_name: string;
  updated_at: string;
  last_run_status: string | null;
};

type OpsStats = {
  knowledge: {
    documents_total: number;
    curated: number;
    clinically_reviewed: number;
    withdrawn: number;
  };
  agents: {
    active: number;
    disabled: number;
  };
  users: {
    total: number;
    active: number;
  };
  sessions: {
    threads_total: number;
    waiting_approval: number;
  };
  recent_threads: RecentThread[];
};

export default function DashboardPage() {
  const [stats, setStats] = useState<OpsStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        setStats(await opsJson<OpsStats>("/api/ops/stats"));
      } catch (err) {
        setError(err instanceof Error ? err.message : "加载失败");
      }
    })();
  }, []);

  return (
    <div className="stack">
      <div>
        <h1 className="page-title">概览</h1>
        <p className="muted page-lead">知识审核、智能体状态与最近会话，一眼看清运营面。</p>
      </div>

      {error ? <p className="error">{error}</p> : null}
      {!stats && !error ? <p className="muted">加载中…</p> : null}

      {stats ? (
        <>
          <div className="stat-grid">
            <div className="stat-card">
              <div className="muted">知识文档</div>
              <strong>{stats.knowledge.documents_total}</strong>
              <span className="muted">
                已策展 {stats.knowledge.curated} · 临床已审 {stats.knowledge.clinically_reviewed} ·
                已撤回 {stats.knowledge.withdrawn}
              </span>
            </div>
            <div className="stat-card">
              <div className="muted">智能体</div>
              <strong>{stats.agents.active + stats.agents.disabled}</strong>
              <span className="muted">
                启用中 {stats.agents.active} · 已禁用 {stats.agents.disabled}
              </span>
            </div>
            <div className="stat-card">
              <div className="muted">用户会话</div>
              <strong>{stats.sessions.threads_total}</strong>
              <span className="muted">
                待审批 {stats.sessions.waiting_approval} · 用户 {stats.users.active}/
                {stats.users.total}
              </span>
            </div>
            <div className="stat-card">
              <div className="muted">待审批</div>
              <strong>{stats.sessions.waiting_approval}</strong>
              <span className="muted">当前仍停在 HITL 的 Run</span>
            </div>
          </div>

          <section className="panel stack">
            <div className="section-head">
              <h2 className="section-title">最近会话</h2>
              <Link href="/sessions" className="linkish">
                全部会话
              </Link>
            </div>
            {stats.recent_threads.length === 0 ? (
              <p className="muted" style={{ margin: 0 }}>
                还没有用户会话。
              </p>
            ) : (
              <div className="doc-list always">
                {stats.recent_threads.map((thread) => (
                  <article key={thread.id} className="doc-card">
                    <Link href={`/sessions/${thread.id}`} className="doc-card__title linkish">
                      {displayTitle(thread.title)}
                    </Link>
                    <div className="doc-card__meta">
                      <span>{thread.user_email ?? "无账号"}</span>
                      <span>{thread.agent_name}</span>
                      <span className={`badge badge--${thread.last_run_status ?? "unknown"}`}>
                        {labelOf(RUN_STATUS_LABELS, thread.last_run_status)}
                      </span>
                      <span>{formatTime(thread.updated_at)}</span>
                    </div>
                  </article>
                ))}
              </div>
            )}
          </section>
        </>
      ) : null}

      <div className="quick-links">
        <Link className="quick-link" href="/knowledge">
          进入知识库
        </Link>
        <Link className="quick-link" href="/agents">
          管理智能体
        </Link>
        <Link className="quick-link" href="/sessions">
          审计会话
        </Link>
        <Link className="quick-link" href="/knowledge/import">
          导入知识
        </Link>
      </div>
    </div>
  );
}
