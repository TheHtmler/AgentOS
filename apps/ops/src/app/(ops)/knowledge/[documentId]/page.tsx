"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { useToast } from "@/components/toast";
import { KnowledgeQuality } from "@/components/knowledge-quality";
import { Button } from "@/components/ui/button";
import { REVIEW_STATUS_HINTS, REVIEW_STATUS_LABELS, SOURCE_KIND_LABELS } from "@/lib/labels";
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
  ontology_terms: Array<{ curie: string; label: string; ontology: string }>;
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
type OntologyCandidate = { curie: string; label: string; ontology: string };

const REVIEW_OPTIONS = ["pending_review", "curated", "clinically_reviewed", "withdrawn"] as const;
const SOURCE_KINDS = [
  "official_reference",
  "clinical_guideline",
  "curated_summary",
  "research_article",
] as const;

function excerpt(text: string, max = 180): string {
  const compact = text.replace(/\s+/g, " ").trim();
  if (compact.length <= max) return compact;
  return `${compact.slice(0, max)}…`;
}

export default function KnowledgeDetailPage() {
  const params = useParams<{ documentId: string }>();
  const router = useRouter();
  const documentId = params.documentId;

  const [doc, setDoc] = useState<DocumentDetail | null>(null);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [snapshotLimit, setSnapshotLimit] = useState(20);
  const [snapshotDetail, setSnapshotDetail] = useState<SnapshotDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [restoringId, setRestoringId] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const [title, setTitle] = useState("");
  const [versionLabel, setVersionLabel] = useState("");
  const [sourceKind, setSourceKind] = useState<string>("curated_summary");
  const [sourceLabel, setSourceLabel] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [sourceDate, setSourceDate] = useState("");
  const [reviewStatus, setReviewStatus] = useState<string>("curated");
  const [ontologyTerms, setOntologyTerms] = useState("[]");
  const [ontologyQuery, setOntologyQuery] = useState("");
  const [ontologyName, setOntologyName] = useState("mondo");
  const [ontologyCandidates, setOntologyCandidates] = useState<OntologyCandidate[]>([]);
  const [resolvingTerms, setResolvingTerms] = useState(false);

  const load = useCallback(async () => {
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
      setOntologyTerms(JSON.stringify(detail.ontology_terms, null, 2));
      const snaps = await opsJson<{ snapshots: Snapshot[] }>(
        `/api/ops/knowledge/documents/${documentId}/snapshots`,
      );
      setSnapshots(snaps.snapshots);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    }
  }, [documentId]);

  useEffect(() => {
    void (async () => {
      await load();
    })();
  }, [load]);

  async function onSave(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const parsedTerms = JSON.parse(ontologyTerms) as unknown;
      if (!Array.isArray(parsedTerms)) throw new Error("术语必须是 JSON 数组");
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
          ontology_terms: parsedTerms,
        }),
      });
      await load();
      toast.show("文档信息已保存");
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function resolveTerms() {
    if (ontologyQuery.trim().length < 2) {
      setError("术语检索词至少需要 2 个字符");
      return;
    }
    setResolvingTerms(true);
    setError(null);
    try {
      const result = await opsJson<{ terms: OntologyCandidate[] }>(
        `/api/ops/knowledge/ontology/resolve?query=${encodeURIComponent(ontologyQuery.trim())}&ontology=${ontologyName}`,
      );
      setOntologyCandidates(result.terms);
    } catch (err) {
      setError(err instanceof Error ? err.message : "术语服务暂不可用");
    } finally {
      setResolvingTerms(false);
    }
  }

  function addOntologyTerm(candidate: OntologyCandidate) {
    try {
      const existing = JSON.parse(ontologyTerms) as unknown;
      if (!Array.isArray(existing)) throw new Error("术语必须是 JSON 数组");
      const terms = existing.filter(
        (term): term is OntologyCandidate =>
          typeof term === "object" &&
          term !== null &&
          "curie" in term &&
          "label" in term &&
          "ontology" in term,
      );
      if (!terms.some((term) => term.curie === candidate.curie)) terms.push(candidate);
      setOntologyTerms(JSON.stringify(terms, null, 2));
    } catch (err) {
      setError(err instanceof Error ? err.message : "术语 JSON 无效");
    }
  }

  async function removeDocument() {
    if (!window.confirm("删除后文档和历史快照都不可恢复。确定删除？")) return;
    setDeleting(true);
    setError(null);
    try {
      await opsJson(`/api/ops/knowledge/documents/${documentId}`, { method: "DELETE" });
      toast.show("文档已删除");
      router.replace("/knowledge");
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除失败");
      setDeleting(false);
    }
  }

  async function restoreSnapshot(snapshotId: string) {
    if (!window.confirm("恢复会覆盖当前正文，并先把当前内容存成新快照。确定恢复？")) return;
    setRestoringId(snapshotId);
    setError(null);
    try {
      await opsJson(`/api/ops/knowledge/documents/${documentId}/snapshots/${snapshotId}/restore`, {
        method: "POST",
      });
      setSnapshotDetail(null);
      await load();
      toast.show("已恢复到该快照");
    } catch (err) {
      setError(err instanceof Error ? err.message : "恢复失败");
    } finally {
      setRestoringId(null);
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
    <div className="stack min-w-0 grid-cols-1 [overflow-wrap:anywhere] [&_.form-grid]:grid-cols-1 md:[&_.form-grid]:grid-cols-2 [&_input]:min-w-0 [&_label]:min-w-0">
      {toast.node}
      <div>
        <Link href="/knowledge" className="crumb">
          ← 知识库
        </Link>
        <div className="page-head">
          <div>
            <h1 className="page-title">{doc?.title ?? "文档详情"}</h1>
            <p className="muted page-lead">标识 {doc?.slug}</p>
          </div>
          <button
            type="button"
            className="danger-link"
            disabled={deleting}
            onClick={() => void removeDocument()}
          >
            {deleting ? "删除中…" : "删除文档"}
          </button>
        </div>
      </div>

      {error ? <p className="error">{error}</p> : null}
      <KnowledgeQuality documentId={documentId} />

      {doc ? (
        <>
          <form
            className="stack min-w-0 grid-cols-1 border-y py-4"
            onSubmit={(event) => void onSave(event)}
          >
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
                状态
                <select value={reviewStatus} onChange={(e) => setReviewStatus(e.target.value)}>
                  {REVIEW_OPTIONS.map((option) => (
                    <option key={option} value={option}>
                      {REVIEW_STATUS_LABELS[option]}
                    </option>
                  ))}
                </select>
                <span className="field-hint">{REVIEW_STATUS_HINTS[reviewStatus]}</span>
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
            <label>
              受控术语（JSON）
              <textarea
                rows={4}
                value={ontologyTerms}
                spellCheck={false}
                onChange={(e) => setOntologyTerms(e.target.value)}
              />
              <span className="field-hint">
                每项包含 curie、label、ontology。保存后 CURIE
                会进入全部切片标签，用于亚型和基因召回。
              </span>
            </label>
            <div className="stack">
              <div className="form-grid cols-2">
                <label>
                  OLS 术语检索
                  <input
                    value={ontologyQuery}
                    placeholder="例如：propionic acidemia"
                    onChange={(e) => setOntologyQuery(e.target.value)}
                  />
                </label>
                <label>
                  本体
                  <select value={ontologyName} onChange={(e) => setOntologyName(e.target.value)}>
                    <option value="mondo">MONDO（疾病）</option>
                    <option value="hp">HPO（表型）</option>
                    <option value="ordo">Orphanet（罕见病）</option>
                  </select>
                </label>
              </div>
              <button
                type="button"
                className="secondary"
                disabled={resolvingTerms}
                onClick={() => void resolveTerms()}
              >
                {resolvingTerms ? "查询中…" : "查询术语"}
              </button>
              {ontologyCandidates.map((candidate) => (
                <button
                  key={candidate.curie}
                  type="button"
                  className="ghost text-left"
                  onClick={() => addOntologyTerm(candidate)}
                >
                  {candidate.label} · {candidate.curie}
                </button>
              ))}
            </div>
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

          <section className="stack min-w-0 grid-cols-1 border-b py-4">
            <h2 className="section-title">内容切片</h2>
            <p className="hint">每条先看摘要；检索用的是完整正文，不是标题。</p>
            {doc.chunks.map((chunk) => {
              const open = expanded[chunk.id] ?? false;
              return (
                <article key={chunk.id} className="doc-card">
                  <div className="doc-card__title">{chunk.title}</div>
                  <div className="doc-card__meta">
                    <span>#{chunk.chunk_index + 1}</span>
                    {chunk.section_label ? <span>{chunk.section_label}</span> : null}
                    {chunk.tags.length > 0 ? <span>{chunk.tags.join("、")}</span> : null}
                  </div>
                  {open ? (
                    <pre className="chunk-body">{chunk.content}</pre>
                  ) : (
                    <p className="chunk-excerpt">{excerpt(chunk.content)}</p>
                  )}
                  <button
                    type="button"
                    className="ghost"
                    onClick={() => setExpanded((prev) => ({ ...prev, [chunk.id]: !open }))}
                  >
                    {open ? "收起全文" : "看全文"}
                  </button>
                </article>
              );
            })}
          </section>

          <section className="stack min-w-0 grid-cols-1 border-b py-4">
            <h2 className="section-title">历史快照</h2>
            {snapshots.length === 0 ? <p className="muted">暂无快照</p> : null}
            {snapshots.slice(0, snapshotLimit).map((snap) => (
              <div key={snap.id} className="snap-card">
                <strong>版本 {snap.version_label ?? "—"}</strong>
                <span className="muted">{new Date(snap.created_at).toLocaleString()}</span>
                <span>创建者：{snap.created_by === "system" ? "系统" : snap.created_by}</span>
                <div className="filter-row">
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => void openSnapshot(snap.id)}
                  >
                    查看快照内容
                  </button>
                  <button
                    type="button"
                    disabled={restoringId === snap.id}
                    onClick={() => void restoreSnapshot(snap.id)}
                  >
                    {restoringId === snap.id ? "恢复中…" : "恢复此版本"}
                  </button>
                </div>
              </div>
            ))}
            {snapshotLimit < snapshots.length ? (
              <Button variant="outline" onClick={() => setSnapshotLimit((value) => value + 20)}>
                显示更多快照（{snapshotLimit}/{snapshots.length}）
              </Button>
            ) : null}
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
