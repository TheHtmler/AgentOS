"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { PageHeader } from "@/components/page-header";
import { Skeleton } from "@/components/skeleton";
import { useToast } from "@/components/toast";
import { REVIEW_STATUS_HINTS, REVIEW_STATUS_LABELS, labelOf } from "@/lib/labels";
import { OpsFetchError, opsJson } from "@/lib/ops-fetch";

type KnowledgeDocument = {
  id: string;
  slug: string;
  title: string;
  source_kind: string;
  version_label: string | null;
  review_status: string;
  chunk_count: number;
  import_status: string;
  import_error: string | null;
  import_progress_done: number | null;
  import_progress_total: number | null;
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
  const toast = useToast();
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("all");
  const [query, setQuery] = useState("");
  const [savingId, setSavingId] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);

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

  // Imports run in the background — keep refreshing while any document is
  // still processing so the status badges reach their terminal state.
  const hasProcessing = documents.some((doc) => doc.import_status === "processing");
  useEffect(() => {
    if (!hasProcessing) return;
    const timer = window.setTimeout(() => void loadDocuments(), 2500);
    return () => window.clearTimeout(timer);
  }, [hasProcessing, loadDocuments, documents]);

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
      toast.show(`已改为${labelOf(REVIEW_STATUS_LABELS, review_status)}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "更新失败");
    } finally {
      setSavingId(null);
    }
  }

  async function removeDocument(documentId: string) {
    if (confirmId !== documentId) {
      setConfirmId(documentId);
      return;
    }
    setSavingId(documentId);
    setError(null);
    try {
      await opsJson(`/api/ops/knowledge/documents/${documentId}`, { method: "DELETE" });
      setDocuments((prev) => prev.filter((row) => row.id !== documentId));
      setConfirmId(null);
      toast.show("文档已删除");
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除失败");
    } finally {
      setSavingId(null);
    }
  }

  return (
    <div className="stack">
      {toast.node}
      <PageHeader
        title="知识库"
        lead="改状态只影响检索；删除会去掉文档和历史快照。"
        actions={
          <Link href="/knowledge/import" className="btn">
            导入
          </Link>
        }
      />

      <p className="hint">
        <strong>待审核</strong> 已入库、对话可搜 · <strong>已审核</strong> 人工复核通过 ·{" "}
        <strong>已下架</strong> 对话搜不到，文件还在
      </p>

      <div className="toolbar">
        <input
          className="search-input"
          value={query}
          placeholder="搜索标题或标识"
          onChange={(event) => setQuery(event.target.value)}
        />
        <div className="seg" role="tablist" aria-label="审核状态">
          {FILTERS.map((item) => (
            <button
              key={item}
              type="button"
              className={filter === item ? "is-selected" : ""}
              onClick={() => setFilter(item)}
            >
              {FILTER_LABELS[item]}
            </button>
          ))}
        </div>
      </div>

      {error ? <p className="error">{error}</p> : null}
      {loading ? <Skeleton /> : null}

      {!loading && visible.length > 0 ? (
        <div className="row-list">
          {visible.map((doc) => (
            <article key={doc.id} className="row row--doc">
              <div>
                <Link href={`/knowledge/${doc.id}`} className="row__title">
                  {doc.title}
                </Link>
                <div className="row__meta">
                  <span>{doc.slug}</span>
                  <span>v{doc.version_label ?? "—"}</span>
                  <span>{doc.chunk_count} 条</span>
                  {doc.import_status === "processing" ? (
                    <span>
                      导入中
                      {doc.import_progress_total
                        ? ` ${doc.import_progress_done ?? 0}/${doc.import_progress_total} 页`
                        : "…"}
                    </span>
                  ) : null}
                  {doc.import_status === "failed" ? (
                    <span className="error" title={doc.import_error ?? undefined}>
                      导入失败
                    </span>
                  ) : null}
                </div>
              </div>
              <select
                className="status-select"
                value={doc.review_status}
                title={REVIEW_STATUS_HINTS[doc.review_status]}
                disabled={savingId === doc.id}
                onChange={(event) => void patchStatus(doc.id, event.target.value)}
              >
                {REVIEW_OPTIONS.map((option) => (
                  <option key={option} value={option}>
                    {REVIEW_STATUS_LABELS[option]}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="danger-link"
                disabled={savingId === doc.id}
                onClick={() => void removeDocument(doc.id)}
                onBlur={() => {
                  if (confirmId === doc.id) setConfirmId(null);
                }}
              >
                {confirmId === doc.id ? "确认删除" : "删除"}
              </button>
            </article>
          ))}
        </div>
      ) : null}

      {!loading && visible.length === 0 ? (
        <div className="empty">没有匹配的文档。可以先导入，或检查筛选条件。</div>
      ) : null}
    </div>
  );
}
