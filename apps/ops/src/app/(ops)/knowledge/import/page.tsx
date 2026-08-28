"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useMemo, useState } from "react";

import { PageHeader } from "@/components/page-header";
import { OpsFetchError, errorMessage as fetchErrorMessage, opsJson } from "@/lib/ops-fetch";

type ImportMode = "json" | "text" | "url" | "file";

type ImportedDocument = {
  id: string;
  slug: string;
  title: string;
  chunk_count: number;
  overwrote: boolean;
  ocr_pages: number;
  text_layer_pages: number;
  import_status: string;
};

type ImportResponse = {
  documents: ImportedDocument[];
};

type DocumentStatus = {
  id: string;
  slug: string;
  title: string;
  chunk_count: number;
  import_status: string;
  import_error: string | null;
  import_progress_done: number | null;
  import_progress_total: number | null;
};

const POLL_INTERVAL_MS = 2000;

const MODES: Array<{ value: ImportMode; label: string }> = [
  { value: "json", label: "JSON" },
  { value: "text", label: "文本" },
  { value: "url", label: "链接" },
  { value: "file", label: "文件" },
];

const JSON_EXAMPLE = JSON.stringify(
  {
    documents: [
      {
        slug: "example-document",
        title: "示例文档",
        chunks: [
          {
            chunk_index: 0,
            title: "第一节",
            content: "文档正文",
          },
        ],
      },
    ],
  },
  null,
  2,
);

function errorMessage(body: unknown, status: number): string {
  if (body && typeof body === "object") {
    return fetchErrorMessage(body as { detail?: string; error?: string }, status);
  }
  return `导入失败（${status}）`;
}

export default function KnowledgeImportPage() {
  const router = useRouter();
  const [mode, setMode] = useState<ImportMode>("json");
  const [jsonBody, setJsonBody] = useState(JSON_EXAMPLE);
  const [slug, setSlug] = useState("");
  const [title, setTitle] = useState("");
  const [textBody, setTextBody] = useState("");
  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [documents, setDocuments] = useState<ImportedDocument[]>([]);
  const [tracked, setTracked] = useState<Record<string, DocumentStatus>>({});

  // Submitted jobs are acknowledged immediately and run in the background —
  // poll the documents list until every submitted slug reaches a terminal state.
  const trackedSlugs = useMemo(() => documents.map((doc) => doc.slug), [documents]);
  useEffect(() => {
    if (trackedSlugs.length === 0) return;
    let cancelled = false;
    let timer: number | undefined;

    async function poll() {
      try {
        const body = await opsJson<{ documents: DocumentStatus[] }>(
          "/api/ops/knowledge/documents?base=mma-pa",
        );
        if (cancelled) return;
        const next: Record<string, DocumentStatus> = {};
        for (const doc of body.documents) {
          if (trackedSlugs.includes(doc.slug)) next[doc.slug] = doc;
        }
        setTracked(next);
        const settled = trackedSlugs.every(
          (slug) => next[slug] && next[slug].import_status !== "processing",
        );
        if (settled) {
          setBusy(false);
        } else {
          timer = window.setTimeout(poll, POLL_INTERVAL_MS);
        }
      } catch {
        if (!cancelled) timer = window.setTimeout(poll, POLL_INTERVAL_MS * 2);
      }
    }

    void poll();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [trackedSlugs]);

  async function submitImport(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setDocuments([]);

    try {
      let body: BodyInit;
      let headers: HeadersInit | undefined;

      if (mode === "file") {
        if (file === null) throw new Error("请选择要导入的文件");
        const form = new FormData();
        form.set("mode", "file");
        form.set("base", "mma-pa");
        if (slug.trim()) form.set("slug", slug.trim());
        if (title.trim()) form.set("title", title.trim());
        form.set("file", file);
        body = form;
      } else {
        let payload: Record<string, unknown>;
        if (mode === "json") {
          payload = {
            mode,
            payload: JSON.parse(jsonBody) as unknown,
            base: "mma-pa",
          };
        } else if (mode === "text") {
          payload = {
            mode,
            slug: slug.trim(),
            title: title.trim(),
            body: textBody,
            base: "mma-pa",
          };
        } else {
          payload = {
            mode,
            url: url.trim(),
            slug: slug.trim(),
            ...(title.trim() ? { title: title.trim() } : {}),
            base: "mma-pa",
          };
        }
        body = JSON.stringify(payload);
        headers = { "Content-Type": "application/json" };
      }

      const response = await fetch("/api/ops/knowledge/import", {
        method: "POST",
        headers,
        body,
      });
      const responseBody = (await response.json().catch(() => null)) as unknown;
      if (!response.ok) {
        throw new OpsFetchError(response.status, errorMessage(responseBody, response.status));
      }
      const accepted = (responseBody as ImportResponse).documents;
      if (accepted.length === 0) setBusy(false);
      setTracked({});
      setDocuments(accepted);
      // busy stays true until the polling effect above sees terminal states.
    } catch (err) {
      if (err instanceof OpsFetchError && err.status === 401) {
        router.replace("/login");
        return;
      }
      if (err instanceof SyntaxError && mode === "json") {
        setError("JSON 格式无效，请检查语法");
      } else {
        setError(err instanceof Error ? err.message : "导入失败");
      }
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <div>
        <Link href="/knowledge" className="crumb">
          ← 知识库
        </Link>
        <PageHeader
          title="导入知识"
          lead="支持文本、链接、PDF 和图片（jpg/png/webp，视觉模型逐页解析）。提交后后台执行，页面会显示进度；相同标识会覆盖并保留快照。"
        />
      </div>

      <div className="seg" role="tablist" aria-label="导入方式">
        {MODES.map((item) => (
          <button
            key={item.value}
            type="button"
            role="tab"
            aria-selected={mode === item.value}
            className={mode === item.value ? "is-selected" : ""}
            disabled={busy}
            onClick={() => {
              setMode(item.value);
              setError(null);
              setDocuments([]);
            }}
          >
            {item.label}
          </button>
        ))}
      </div>

      <form className="panel stack" onSubmit={submitImport}>
        {mode === "json" ? (
          <label>
            <span className="req">*</span>Seed 格式 JSON
            <textarea
              rows={16}
              value={jsonBody}
              required
              spellCheck={false}
              onChange={(event) => setJsonBody(event.target.value)}
            />
            <span className="field-hint">
              批量导入的文档数组；每个文档含 slug、title 和 chunks（切片）。
            </span>
          </label>
        ) : null}

        {mode === "text" ? (
          <>
            <div className="form-grid cols-2">
              <label>
                <span className="req">*</span>文档标识
                <input
                  value={slug}
                  required
                  placeholder="例如：mma-guideline"
                  onChange={(event) => setSlug(event.target.value)}
                />
                <span className="field-hint">
                  文档的唯一标识；相同标识再导入会覆盖旧版并保留快照。
                </span>
              </label>
              <label>
                <span className="req">*</span>标题
                <input
                  value={title}
                  required
                  placeholder="文档标题"
                  onChange={(event) => setTitle(event.target.value)}
                />
              </label>
            </div>
            <label>
              <span className="req">*</span>正文
              <textarea
                rows={14}
                value={textBody}
                required
                placeholder="贴入纯文本或 Markdown 内容"
                onChange={(event) => setTextBody(event.target.value)}
              />
            </label>
          </>
        ) : null}

        {mode === "url" ? (
          <>
            <label>
              <span className="req">*</span>网页链接
              <input
                type="url"
                value={url}
                required
                placeholder="https://example.com/article"
                onChange={(event) => setUrl(event.target.value)}
              />
              <span className="field-hint">
                抓取网页正文后导入；抓取失败会显示在文档的失败状态里。
              </span>
            </label>
            <div className="form-grid cols-2">
              <label>
                <span className="req">*</span>文档标识
                <input
                  value={slug}
                  required
                  placeholder="例如：source-article"
                  onChange={(event) => setSlug(event.target.value)}
                />
                <span className="field-hint">唯一标识；相同标识再导入会覆盖并保留快照。</span>
              </label>
              <label>
                标题（可选）
                <input
                  value={title}
                  placeholder="留空则使用网页标题"
                  onChange={(event) => setTitle(event.target.value)}
                />
              </label>
            </div>
          </>
        ) : null}

        {mode === "file" ? (
          <>
            <label>
              文件
              <input
                type="file"
                required
                accept=".txt,.md,.json,.pdf,.jpg,.jpeg,.png,.webp,text/plain,text/markdown,application/json,application/pdf,image/jpeg,image/png,image/webp"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
              <span className="field-hint">
                txt / md / json / pdf / jpg / png / webp。PDF 和图片由视觉模型逐页转录。
              </span>
            </label>
            <div className="form-grid cols-2">
              <label>
                文档标识（可选）
                <input
                  value={slug}
                  placeholder="留空则根据文件名生成"
                  onChange={(event) => setSlug(event.target.value)}
                />
                <span className="field-hint">相同标识再导入会覆盖旧版并保留快照。</span>
              </label>
              <label>
                标题（可选）
                <input
                  value={title}
                  placeholder="留空则使用文件名"
                  onChange={(event) => setTitle(event.target.value)}
                />
              </label>
            </div>
          </>
        ) : null}

        {error ? <p className="error">{error}</p> : null}
        <button type="submit" disabled={busy}>
          {busy ? "导入中…" : "开始导入"}
        </button>
      </form>

      {documents.length > 0 ? (
        <section className="panel stack" aria-live="polite">
          <h2 className="section-title">导入任务</h2>
          <div className="doc-list always">
            {documents.map((document) => {
              const status = tracked[document.slug];
              const state = status?.import_status ?? "processing";
              return (
                <article key={document.id} className="doc-card">
                  {state === "ready" && status ? (
                    <Link href={`/knowledge/${status.id}`} className="doc-card__title linkish">
                      {status.title}
                    </Link>
                  ) : (
                    <span className="doc-card__title">{status?.title ?? document.title}</span>
                  )}
                  <div className="muted">标识：{document.slug}</div>
                  <div className="doc-card__meta">
                    {state === "processing" ? (
                      <span>
                        导入中
                        {status?.import_progress_total
                          ? `：第 ${status.import_progress_done ?? 0}/${status.import_progress_total} 页`
                          : "…"}
                      </span>
                    ) : null}
                    {state === "ready" && status ? (
                      <>
                        <span>{status.chunk_count} 条切片</span>
                        <span>{document.overwrote ? "已覆盖原文档" : "新建文档"}</span>
                      </>
                    ) : null}
                    {state === "failed" ? (
                      <span className="error">导入失败：{status?.import_error ?? "未知错误"}</span>
                    ) : null}
                  </div>
                </article>
              );
            })}
          </div>
          {!busy ? (
            <p className="muted">重复提交相同标识会覆盖并保留快照，可在本页继续导入。</p>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}
