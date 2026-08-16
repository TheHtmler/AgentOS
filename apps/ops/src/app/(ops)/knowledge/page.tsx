"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { OpsFetchError, opsJson } from "@/lib/ops-fetch";
import { REVIEW_STATUS_LABELS, labelOf } from "@/lib/labels";

type KnowledgeDocument = {
  id: string;
  slug: string;
  title: string;
  source_kind: string;
  version_label: string | null;
  review_status: string;
  chunk_count: number;
};

const REVIEW_OPTIONS = ["curated", "clinically_reviewed", "withdrawn"] as const;
const FILTERS = ["all", ...REVIEW_OPTIONS] as const;

const FILTER_LABELS: Record<(typeof FILTERS)[number], string> = {
  all: "全部",
  curated: REVIEW_STATUS_LABELS.curated,
  clinically_reviewed: REVIEW_STATUS_LABELS.clinically_reviewed,
  withdrawn: REVIEW_STATUS_LABELS.withdrawn,
};

export default function KnowledgePage() {
  const router = useRouter();
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("all");
  const [query, setQuery] = useState("");
  const [savingId, setSavingId] = useState<string | null>(null);

  const loadDocuments = useCallback(async () => {
    try {
      const body = await opsJson<{ documents: KnowledgeDocument[] }>(
        "/api/ops/knowledge/documents?base=mma-pa",
      );
      setDocuments(body.documents);
      setError(null);
    } catch (err) {
      if (err instanceof OpsFetchError && err.status === 401) {
        router.replace("/login");
        return;
      }
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    void (async () => {
      await loadDocuments();
    })();
  }, [loadDocuments]);

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return documents.filter((doc) => {
      if (filter !== "all" && doc.review_status !== filter) return false;
      if (!needle) return true;
      return doc.title.toLowerCase().includes(needle) || doc.slug.toLowerCase().includes(needle);
    });
  }, [documents, filter, query]);

  async function patchStatus(documentId: string, review_status: string) {
    setSavingId(documentId);
    setError(null);
    try {
      const updated = await opsJson<KnowledgeDocument>(
        `/api/ops/knowledge/documents/${documentId}`,
        {
          method: "PATCH",
          body: JSON.stringify({ review_status }),
        },
      );
      setDocuments((prev) =>
        prev.map((row) => (row.id === updated.id ? { ...row, ...updated } : row)),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "更新失败");
    } finally {
      setSavingId(null);
    }
  }

  return (
    <div className="stack">
      <div>
        <h1 className="page-title">知识库</h1>
        <p className="muted page-lead">MMA/PA 公共知识 · 审核状态与文档元数据</p>
      </div>

      <section className="callout" aria-label="知识库能力说明">
        <h2>现在能做什么 / 数据从哪来</h2>
        <ul>
          <li>
            <strong>已有：</strong>可通过
            JSON、文本、网页链接或文件导入知识，并查看内容切片、修改审核状态与元数据、查看历史快照。
          </li>
          <li>
            <strong>覆盖规则：</strong>相同文档标识会更新原文档，并自动保留覆盖前的快照。
          </li>
          <li>
            <strong>初始数据：</strong>仓库策展文件{" "}
            <code>services/agent-api/seed/knowledge/mma_pa_chunks.json</code>
            （约 4 篇文档 / 32 条切片），经 Mac mini 上执行{" "}
            <code>uv run --directory services/agent-api python scripts/seed_knowledge.py</code>{" "}
            写入数据库；后续内容可直接从导入页补充。
          </li>
        </ul>
      </section>

      <div className="filter-row">
        <Link href="/knowledge/import" className="quick-link">
          导入
        </Link>
        <input
          className="search-input"
          value={query}
          placeholder="筛选标题或标识"
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>

      <div className="filter-row">
        {FILTERS.map((item) => (
          <button
            key={item}
            type="button"
            className={`secondary ${filter === item ? "is-selected" : ""}`}
            onClick={() => setFilter(item)}
          >
            {FILTER_LABELS[item]}
          </button>
        ))}
      </div>

      {error ? <p className="error">{error}</p> : null}
      {loading ? <p className="muted">加载中…</p> : null}

      {!loading && visible.length > 0 ? (
        <div className="doc-list always">
          {visible.map((doc) => (
            <article key={doc.id} className="doc-card">
              <Link href={`/knowledge/${doc.id}`} className="doc-card__title linkish">
                {doc.title}
              </Link>
              <div className="muted" style={{ fontSize: "0.85rem", wordBreak: "break-all" }}>
                标识：{doc.slug}
              </div>
              <div className="doc-card__meta">
                <span>版本 {doc.version_label ?? "—"}</span>
                <span className={`badge badge--${doc.review_status}`}>
                  {labelOf(REVIEW_STATUS_LABELS, doc.review_status)}
                </span>
                <span>{doc.chunk_count} 条切片</span>
              </div>
              <label>
                审核状态
                <select
                  value={doc.review_status}
                  disabled={savingId === doc.id}
                  onChange={(event) => void patchStatus(doc.id, event.target.value)}
                >
                  {REVIEW_OPTIONS.map((option) => (
                    <option key={option} value={option}>
                      {REVIEW_STATUS_LABELS[option]}
                    </option>
                  ))}
                </select>
              </label>
            </article>
          ))}
        </div>
      ) : null}

      {!loading && visible.length === 0 ? (
        <div className="panel">
          <p className="muted" style={{ margin: 0 }}>
            暂无文档。若库是空的，请在 API 机器上跑一次知识 seed（见上方说明）。
          </p>
        </div>
      ) : null}
    </div>
  );
}
