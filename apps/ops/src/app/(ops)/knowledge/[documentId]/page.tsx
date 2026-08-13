"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { opsJson } from "@/lib/ops-fetch";

type Chunk = {
  id: string;
  chunk_index: number;
  title: string;
  content: string;
  section_label: string | null;
  tags: string[];
};

type DocumentDetail = {
  id: string;
  slug: string;
  title: string;
  source_kind: string;
  source_url: string | null;
  source_label: string | null;
  source_date: string | null;
  version_label: string | null;
  review_status: string;
  reviewed_at: string | null;
  chunk_count: number;
  chunks: Chunk[];
};

type Snapshot = {
  id: string;
  version_label: string | null;
  created_at: string;
  created_by: string;
};

type SnapshotDetail = Snapshot & { payload: Record<string, unknown> };

const REVIEW_OPTIONS = ["curated", "clinically_reviewed", "withdrawn"] as const;
const SOURCE_KINDS = ["official_reference", "clinical_guideline", "curated_summary"] as const;

export default function KnowledgeDetailPage() {
  const params = useParams<{ documentId: string }>();
  const router = useRouter();
  const documentId = params.documentId;

  const [doc, setDoc] = useState<DocumentDetail | null>(null);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [snapshotDetail, setSnapshotDetail] = useState<SnapshotDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const [title, setTitle] = useState("");
  const [versionLabel, setVersionLabel] = useState("");
  const [sourceKind, setSourceKind] = useState<string>("curated_summary");
  const [sourceLabel, setSourceLabel] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [sourceDate, setSourceDate] = useState("");
  const [reviewStatus, setReviewStatus] = useState<string>("curated");

  const load = useCallback(async () => {
    setError(null);
    try {
      const detail = await opsJson<DocumentDetail>(`/api/ops/knowledge/documents/${documentId}`);
      setDoc(detail);
      setTitle(detail.title);
      setVersionLabel(detail.version_label ?? "");
      setSourceKind(detail.source_kind);
      setSourceLabel(detail.source_label ?? "");
      setSourceUrl(detail.source_url ?? "");
      setSourceDate(detail.source_date ?? "");
      setReviewStatus(detail.review_status);
      const snaps = await opsJson<{ snapshots: Snapshot[] }>(
        `/api/ops/knowledge/documents/${documentId}/snapshots`,
      );
      setSnapshots(snaps.snapshots);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    }
  }, [documentId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function onSave(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await opsJson(`/api/ops/knowledge/documents/${documentId}`, {
        method: "PATCH",
        body: JSON.stringify({
          title,
          version_label: versionLabel || null,
          source_kind: sourceKind,
          source_label: sourceLabel || null,
          source_url: sourceUrl || null,
          source_date: sourceDate || null,
          review_status: reviewStatus,
        }),
      });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function openSnapshot(snapshotId: string) {
    setError(null);
    try {
      setSnapshotDetail(
        await opsJson<SnapshotDetail>(
          `/api/ops/knowledge/documents/${documentId}/snapshots/${snapshotId}`,
        ),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "快照加载失败");
    }
  }

  if (!doc && !error) {
    return <p className="muted">加载中…</p>;
  }

  return (
    <div className="stack">
      <div>
        <Link href="/knowledge" className="muted">
          ← 返回列表
        </Link>
        <h1 className="page-title">{doc?.title ?? "文档详情"}</h1>
        <p className="muted">{doc?.slug}</p>
      </div>

      {error ? <p className="error">{error}</p> : null}

      {doc ? (
        <>
          <form className="panel stack" onSubmit={(event) => void onSave(event)}>
            <h2 className="section-title">元数据</h2>
            <label>
              标题
              <input value={title} onChange={(e) => setTitle(e.target.value)} required />
            </label>
            <label>
              版本标签
              <input value={versionLabel} onChange={(e) => setVersionLabel(e.target.value)} />
            </label>
            <label>
              来源类型
              <select value={sourceKind} onChange={(e) => setSourceKind(e.target.value)}>
                {SOURCE_KINDS.map((kind) => (
                  <option key={kind} value={kind}>
                    {kind}
                  </option>
                ))}
              </select>
            </label>
            <label>
              来源标签
              <input value={sourceLabel} onChange={(e) => setSourceLabel(e.target.value)} />
            </label>
            <label>
              来源 URL
              <input value={sourceUrl} onChange={(e) => setSourceUrl(e.target.value)} />
            </label>
            <label>
              来源日期
              <input value={sourceDate} onChange={(e) => setSourceDate(e.target.value)} />
            </label>
            <label>
              审核状态
              <select value={reviewStatus} onChange={(e) => setReviewStatus(e.target.value)}>
                {REVIEW_OPTIONS.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            </label>
            <p className="muted" style={{ margin: 0 }}>
              reviewed_at：{doc.reviewed_at ? new Date(doc.reviewed_at).toLocaleString() : "—"} ·
              chunks：{doc.chunk_count}
            </p>
            <button type="submit" disabled={saving}>
              {saving ? "保存中…" : "保存"}
            </button>
          </form>

          <section className="panel stack">
            <h2 className="section-title">Chunks（只读）</h2>
            {doc.chunks.map((chunk) => {
              const open = expanded[chunk.id] ?? false;
              return (
                <article key={chunk.id} className="doc-card">
                  <div className="doc-card__title">
                    #{chunk.chunk_index} {chunk.title}
                  </div>
                  <div className="doc-card__meta">
                    <span>{chunk.section_label ?? "—"}</span>
                    <span>{chunk.tags.join(", ") || "无 tags"}</span>
                  </div>
                  <button
                    type="button"
                    className="secondary block"
                    onClick={() => setExpanded((prev) => ({ ...prev, [chunk.id]: !open }))}
                  >
                    {open ? "收起内容" : "展开内容"}
                  </button>
                  {open ? <pre className="chunk-body">{chunk.content}</pre> : null}
                </article>
              );
            })}
          </section>

          <section className="panel stack">
            <h2 className="section-title">快照（只读）</h2>
            {snapshots.length === 0 ? <p className="muted">暂无快照</p> : null}
            {snapshots.map((snap) => (
              <div key={snap.id} className="snap-card">
                <strong>{snap.version_label ?? "—"}</strong>
                <span className="muted">{new Date(snap.created_at).toLocaleString()}</span>
                <span>{snap.created_by}</span>
                <button type="button" className="secondary" onClick={() => void openSnapshot(snap.id)}>
                  查看 payload
                </button>
              </div>
            ))}
            {snapshotDetail ? (
              <pre className="chunk-body">
                {JSON.stringify(snapshotDetail.payload, null, 2)}
              </pre>
            ) : null}
          </section>
        </>
      ) : null}

      <button type="button" className="secondary" onClick={() => router.push("/knowledge")}>
        返回列表
      </button>
    </div>
  );
}
