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
  const [savingId, setSavingId] = useState<string | null>(null);

  const loadDocuments = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const body = await opsJson<{ documents: KnowledgeDocument[] }>(
        "/api/ops/knowledge/documents?base=mma-pa",
      );
      setDocuments(body.documents);
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
    void loadDocuments();
  }, [loadDocuments]);

  const visible = useMemo(
    () => (filter === "all" ? documents : documents.filter((doc) => doc.review_status === filter)),
    [documents, filter],
  );

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
      setDocuments((prev) => prev.map((row) => (row.id === updated.id ? { ...row, ...updated } : row)));
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
        <p className="muted">MMA/PA 公共知识 · 审核与文档详情</p>
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
                <span className={`status-${doc.review_status}`}>
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

      {!loading && visible.length === 0 ? <p className="muted">暂无文档</p> : null}
    </div>
  );
}
