"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { opsJson } from "@/lib/ops-fetch";
import { REVIEW_STATUS_LABELS, SOURCE_KIND_LABELS } from "@/lib/labels";

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
        <p className="muted">标识：{doc?.slug}</p>
      </div>

      {error ? <p className="error">{error}</p> : null}

      {doc ? (
        <>
          <form className="panel stack" onSubmit={(event) => void onSave(event)}>
            <h2 className="section-title">文档信息</h2>
            <div className="form-grid cols-2">
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
                      {SOURCE_KIND_LABELS[kind]}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                审核状态
                <select value={reviewStatus} onChange={(e) => setReviewStatus(e.target.value)}>
                  {REVIEW_OPTIONS.map((option) => (
                    <option key={option} value={option}>
                      {REVIEW_STATUS_LABELS[option]}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                来源名称
                <input value={sourceLabel} onChange={(e) => setSourceLabel(e.target.value)} />
              </label>
              <label>
                来源日期
                <input value={sourceDate} onChange={(e) => setSourceDate(e.target.value)} />
              </label>
            </div>
            <label>
              来源链接
              <input value={sourceUrl} onChange={(e) => setSourceUrl(e.target.value)} />
            </label>
            <p className="muted" style={{ margin: 0 }}>
              审核时间：
              {doc.reviewed_at ? new Date(doc.reviewed_at).toLocaleString() : "—"}
              {" · "}
              切片数：{doc.chunk_count}
            </p>
            <button type="submit" disabled={saving}>
              {saving ? "保存中…" : "保存"}
            </button>
          </form>

          <section className="panel stack">
            <h2 className="section-title">内容切片（只读）</h2>
            {doc.chunks.map((chunk) => {
              const open = expanded[chunk.id] ?? false;
              return (
                <article key={chunk.id} className="doc-card">
                  <div className="doc-card__title">
                    第 {chunk.chunk_index} 条 · {chunk.title}
                  </div>
                  <div className="doc-card__meta">
                    <span>章节：{chunk.section_label ?? "—"}</span>
                    <span>标签：{chunk.tags.join("、") || "无"}</span>
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
            <h2 className="section-title">历史快照（只读）</h2>
            {snapshots.length === 0 ? <p className="muted">暂无快照</p> : null}
            {snapshots.map((snap) => (
              <div key={snap.id} className="snap-card">
                <strong>版本 {snap.version_label ?? "—"}</strong>
                <span className="muted">{new Date(snap.created_at).toLocaleString()}</span>
                <span>创建者：{snap.created_by === "system" ? "系统" : snap.created_by}</span>
                <button type="button" className="secondary" onClick={() => void openSnapshot(snap.id)}>
                  查看快照内容
                </button>
              </div>
            ))}
            {snapshotDetail ? (
              <pre className="chunk-body">{JSON.stringify(snapshotDetail.payload, null, 2)}</pre>
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
