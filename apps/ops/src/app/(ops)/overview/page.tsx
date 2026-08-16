"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { PageHeader } from "@/components/page-header";
import { Skeleton } from "@/components/skeleton";
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

export default function OverviewPage() {
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
      <PageHeader
        title="概览"
        lead="先看审核进度和待审批，再进知识库或会话处理。"
        actions={
          <Link href="/knowledge/import" className="btn">
            导入知识
          </Link>
        }
      />

      {error ? <p className="error">{error}</p> : null}
      {!stats && !error ? <Skeleton rows={3} /> : null}

      {stats ? (
        <>
          <div className="stat-grid">
            <Link className="stat-card" href="/knowledge">
              <div className="muted">知识文档</div>
              <strong>{stats.knowledge.documents_total}</strong>
              <span className="muted">
                策展 {stats.knowledge.curated} · 已审 {stats.knowledge.clinically_reviewed} · 撤回{" "}
                {stats.knowledge.withdrawn}
              </span>
            </Link>
            <Link className="stat-card" href="/agents">
              <div className="muted">智能体</div>
              <strong>{stats.agents.active + stats.agents.disabled}</strong>
              <span className="muted">
                启用 {stats.agents.active} · 禁用 {stats.agents.disabled}
              </span>
            </Link>
            <Link className="stat-card" href="/sessions">
              <div className="muted">用户会话</div>
              <strong>{stats.sessions.threads_total}</strong>
              <span className="muted">
                用户 {stats.users.active}/{stats.users.total}
              </span>
            </Link>
            <Link className="stat-card" href="/sessions?run_status=waiting_approval">
              <div className="muted">待审批</div>
              <strong>{stats.sessions.waiting_approval}</strong>
              <span className="muted">仍停在 HITL 的 Run</span>
            </Link>
          </div>

          <section className="panel stack">
            <div className="section-head">
              <h2 className="section-title">最近会话</h2>
              <Link href="/sessions" className="linkish">
                全部会话
              </Link>
            </div>
            {stats.recent_threads.length === 0 ? (
              <p className="empty">还没有用户会话。</p>
            ) : (
              <div className="row-list">
                {stats.recent_threads.map((thread) => (
                  <Link key={thread.id} href={`/sessions/${thread.id}`} className="row">
                    <div>
                      <div className="row__title">{displayTitle(thread.title)}</div>
                      <div className="row__meta">
                        <span>{thread.user_email ?? "无账号"}</span>
                        <span>{thread.agent_name}</span>
                      </div>
                    </div>
                    <span className={`badge badge--${thread.last_run_status ?? "unknown"}`}>
                      {labelOf(RUN_STATUS_LABELS, thread.last_run_status)}
                    </span>
                    <span className="muted">{formatTime(thread.updated_at)}</span>
                  </Link>
                ))}
              </div>
            )}
          </section>
        </>
      ) : null}
    </div>
  );
}
