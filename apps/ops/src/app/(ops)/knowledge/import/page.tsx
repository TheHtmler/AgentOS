"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";

import { OpsFetchError } from "@/lib/ops-fetch";

type ImportMode = "json" | "text" | "url" | "file";

type ImportedDocument = {
  id: string;
  slug: string;
  title: string;
  chunk_count: number;
  overwrote: boolean;
  ocr_pages: number;
  text_layer_pages: number;
};

type ImportResponse = {
  documents: ImportedDocument[];
};

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
    const detail = "detail" in body ? body.detail : undefined;
    const error = "error" in body ? body.error : undefined;
    if (typeof detail === "string") return detail;
    if (typeof error === "string") return error;
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
      setDocuments((responseBody as ImportResponse).documents);
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
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <div>
        <Link href="/knowledge" className="linkish">
          ← 返回知识库
        </Link>
        <h1 className="page-title">导入知识</h1>
        <p className="muted page-lead">选择来源，将内容写入 MMA/PA 公共知识库。</p>
      </div>

      <section className="callout" aria-label="导入说明">
        <h2>导入前请确认</h2>
        <p>相同标识会覆盖现有文档，并保留覆盖前的历史快照。PDF 可能需要 OCR，处理时间会较长。</p>
      </section>

      <div className="filter-row" role="tablist" aria-label="导入方式">
        {MODES.map((item) => (
          <button
            key={item.value}
            type="button"
            role="tab"
            aria-selected={mode === item.value}
            className={`secondary ${mode === item.value ? "is-selected" : ""}`}
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
            Seed 格式 JSON
            <textarea
              rows={16}
              value={jsonBody}
              required
              spellCheck={false}
              onChange={(event) => setJsonBody(event.target.value)}
            />
          </label>
        ) : null}

        {mode === "text" ? (
          <>
            <div className="form-grid cols-2">
              <label>
                文档标识
                <input
                  value={slug}
                  required
                  placeholder="例如：mma-guideline"
                  onChange={(event) => setSlug(event.target.value)}
                />
              </label>
              <label>
                标题
                <input
                  value={title}
                  required
                  placeholder="文档标题"
                  onChange={(event) => setTitle(event.target.value)}
                />
              </label>
            </div>
            <label>
              正文
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
              网页链接
              <input
                type="url"
                value={url}
                required
                placeholder="https://example.com/article"
                onChange={(event) => setUrl(event.target.value)}
              />
            </label>
            <div className="form-grid cols-2">
              <label>
                文档标识
                <input
                  value={slug}
                  required
                  placeholder="例如：source-article"
                  onChange={(event) => setSlug(event.target.value)}
                />
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
                accept=".txt,.md,.json,.pdf,text/plain,text/markdown,application/json,application/pdf"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
            </label>
            <div className="form-grid cols-2">
              <label>
                文档标识（可选）
                <input
                  value={slug}
                  placeholder="留空则根据文件名生成"
                  onChange={(event) => setSlug(event.target.value)}
                />
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
          <h2 className="section-title">导入完成</h2>
          <div className="doc-list always">
            {documents.map((document) => (
              <article key={document.id} className="doc-card">
                <Link href={`/knowledge/${document.id}`} className="doc-card__title linkish">
                  {document.title}
                </Link>
                <div className="muted">标识：{document.slug}</div>
                <div className="doc-card__meta">
                  <span>{document.chunk_count} 条切片</span>
                  <span>{document.overwrote ? "已覆盖原文档" : "新建文档"}</span>
                  <span>文本层 {document.text_layer_pages} 页</span>
                  <span>OCR {document.ocr_pages} 页</span>
                </div>
              </article>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}
