"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { opsJson } from "@/lib/ops-fetch";

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
        <p className="muted">运营控制台 · 知识库与 Agent</p>
      </div>

      {error ? <p className="error">{error}</p> : null}
      {!stats && !error ? <p className="muted">加载中…</p> : null}

      {stats ? (
        <div className="stat-grid">
          <div className="stat-card">
            <div className="muted">知识文档</div>
            <strong>{stats.knowledge.documents_total}</strong>
            <span className="muted">
              curated {stats.knowledge.curated} · reviewed {stats.knowledge.clinically_reviewed} ·
              withdrawn {stats.knowledge.withdrawn}
            </span>
          </div>
          <div className="stat-card">
            <div className="muted">Agents</div>
            <strong>{stats.agents.active + stats.agents.disabled}</strong>
            <span className="muted">
              active {stats.agents.active} · disabled {stats.agents.disabled}
            </span>
          </div>
        </div>
      ) : null}

      <div className="quick-links">
        <Link className="quick-link" href="/knowledge">
          进入知识库
        </Link>
        <Link className="quick-link" href="/agents">
          管理 Agents
        </Link>
      </div>
    </div>
  );
}
