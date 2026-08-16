"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { PageHeader } from "@/components/page-header";
import { Skeleton } from "@/components/skeleton";
import { useToast } from "@/components/toast";
import { REVIEW_STATUS_LABELS, labelOf } from "@/lib/labels";
import { OpsFetchError, opsJson } from "@/lib/ops-fetch";

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
  const toast = useToast();
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
      toast.show("审核状态已更新");
    } catch (err) {
      setError(err instanceof Error ? err.message : "更新失败");
    } finally {
      setSavingId(null);
    }
  }

  return (
    <div className="stack">
      {toast.node}
      <PageHeader
        title="知识库"
        lead="审核公共知识、改状态，或导入新文档。"
        actions={
          <Link href="/knowledge/import" className="btn">
            导入文档
          </Link>
        }
      />

      <details className="callout">
        <summary>导入与覆盖规则</summary>
        <ul>
          <li>支持 JSON、文本、链接和文件；相同标识会覆盖并保留快照。</li>
          <li>
            初始数据来自 <code>seed/knowledge/mma_pa_chunks.json</code>，用{" "}
            <code>scripts/seed_knowledge.py</code> 写入。
          </li>
        </ul>
      </details>

      <div className="toolbar">
        <input
          className="search-input"
          value={query}
          placeholder="筛选标题或标识"
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
            <article key={doc.id} className="row">
              <div>
                <Link href={`/knowledge/${doc.id}`} className="row__title linkish">
                  {doc.title}
                </Link>
                <div className="row__meta">
                  <span>{doc.slug}</span>
                  <span>v{doc.version_label ?? "—"}</span>
                  <span>{doc.chunk_count} 条切片</span>
                </div>
              </div>
              <span className={`badge badge--${doc.review_status}`}>
                {labelOf(REVIEW_STATUS_LABELS, doc.review_status)}
              </span>
              <label>
                改状态
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
        <div className="empty">没有匹配的文档。可以先导入，或检查筛选条件。</div>
      ) : null}
    </div>
  );
}
