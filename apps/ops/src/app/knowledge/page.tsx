"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

type KnowledgeDocument = {
  id: string;
  slug: string;
  title: string;
  source_kind: string;
  version_label: string | null;
  review_status: string;
  chunk_count: number;
};

type Snapshot = {
  id: string;
  version_label: string | null;
  created_at: string;
  created_by: string;
};

const REVIEW_OPTIONS = ["curated", "clinically_reviewed", "withdrawn"] as const;

export default function KnowledgePage() {
  const router = useRouter();
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [savingId, setSavingId] = useState<string | null>(null);

  const loadDocuments = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch("/api/ops/knowledge/documents?base=mma-pa", {
        cache: "no-store",
      });
      if (response.status === 401) {
        router.replace("/login");
        return;
      }
      if (!response.ok) {
        setError(`加载文档失败（${response.status}）`);
        return;
      }
      const body = (await response.json()) as { documents: KnowledgeDocument[] };
      setDocuments(body.documents);
    } catch {
      setError("无法连接运营 API");
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    void loadDocuments();
  }, [loadDocuments]);

  async function logout() {
    await fetch("/api/ops/logout", { method: "POST" });
    router.replace("/login");
    router.refresh();
  }

  async function patchStatus(documentId: string, review_status: string) {
    setSavingId(documentId);
    setError(null);
    try {
      const response = await fetch(`/api/ops/knowledge/documents/${documentId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ review_status }),
      });
      if (!response.ok) {
        setError(`更新状态失败（${response.status}）`);
        return;
      }
      const updated = (await response.json()) as KnowledgeDocument;
      setDocuments((prev) => prev.map((row) => (row.id === updated.id ? { ...row, ...updated } : row)));
    } finally {
      setSavingId(null);
    }
  }

  async function openSnapshots(documentId: string) {
    setSelectedId(documentId);
    setSnapshots([]);
    const response = await fetch(`/api/ops/knowledge/documents/${documentId}/snapshots`, {
      cache: "no-store",
    });
    if (!response.ok) {
      setError(`加载快照失败（${response.status}）`);
      return;
    }
    const body = (await response.json()) as { snapshots: Snapshot[] };
    setSnapshots(body.snapshots);
  }

  return (
    <div className="shell">
      <header className="topbar">
        <div>
          <div className="brand">AgentOS Ops</div>
          <p className="muted">知识库审核 · mma-pa</p>
        </div>
        <button type="button" className="secondary" onClick={() => void logout()}>
          退出
        </button>
      </header>

      <section className="panel stack">
        <div>
          <h1 style={{ margin: "0 0 6px", fontSize: "1.2rem" }}>公共知识文档</h1>
          <p className="muted" style={{ margin: 0 }}>
            修改 review_status；快照只读，由 seed/upsert 自动生成。
          </p>
        </div>

        {error ? <p className="error">{error}</p> : null}
        {loading ? <p className="muted">加载中…</p> : null}

        {!loading && documents.length > 0 ? (
          <table>
            <thead>
              <tr>
                <th>标题</th>
                <th>版本</th>
                <th>状态</th>
                <th>切片</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {documents.map((doc) => (
                <tr key={doc.id}>
                  <td>
                    <div>{doc.title}</div>
                    <div className="muted">{doc.slug}</div>
                  </td>
                  <td>{doc.version_label ?? "—"}</td>
                  <td className={`status-${doc.review_status}`}>{doc.review_status}</td>
                  <td>{doc.chunk_count}</td>
                  <td>
                    <div className="stack" style={{ gap: 8 }}>
                      <select
                        value={doc.review_status}
                        disabled={savingId === doc.id}
                        onChange={(event) => void patchStatus(doc.id, event.target.value)}
                      >
                        {REVIEW_OPTIONS.map((option) => (
                          <option key={option} value={option}>
                            {option}
                          </option>
                        ))}
                      </select>
                      <button
                        type="button"
                        className="secondary"
                        onClick={() => void openSnapshots(doc.id)}
                      >
                        查看快照
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}

        {!loading && documents.length === 0 ? <p className="muted">暂无文档</p> : null}
      </section>

      {selectedId ? (
        <section className="panel stack" style={{ marginTop: 16 }}>
          <h2 style={{ margin: 0, fontSize: "1.05rem" }}>快照（只读）</h2>
          {snapshots.length === 0 ? (
            <p className="muted">该文档尚无快照</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>版本</th>
                  <th>创建时间</th>
                  <th>创建者</th>
                </tr>
              </thead>
              <tbody>
                {snapshots.map((snap) => (
                  <tr key={snap.id}>
                    <td>{snap.version_label ?? "—"}</td>
                    <td>{new Date(snap.created_at).toLocaleString()}</td>
                    <td>{snap.created_by}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      ) : null}
    </div>
  );
}
