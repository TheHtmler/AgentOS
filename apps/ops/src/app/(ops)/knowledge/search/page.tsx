"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { ArrowLeft, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { opsJson } from "@/lib/ops-fetch";

type Hit = {
  chunk_id: string;
  document_id: string;
  document_title: string;
  title: string;
  content: string;
  section_label: string | null;
  keyword_rank?: number;
  vector_rank?: number;
  vector_score: number;
  adjacent: Array<{ chunk_id: string; content: string }>;
};
type Result = {
  results: Hit[];
  duration_ms: number;
  diagnostics: {
    scanned_chunks: number;
    usable_vector_chunks: number;
    degraded_reason: string | null;
  };
};
export default function KnowledgeSearchPage() {
  const [query, setQuery] = useState("");
  const [agent, setAgent] = useState("imd");
  const [tags, setTags] = useState("");
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function search(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setResult(null);
    try {
      setResult(
        await opsJson<Result>("/api/ops/knowledge/search", {
          method: "POST",
          body: JSON.stringify({ query, agent_slug: agent, disease_tags: tags }),
        }),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "检索失败");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="space-y-5">
      <Button asChild variant="ghost" size="sm">
        <Link href="/knowledge">
          <ArrowLeft />
          知识库
        </Link>
      </Button>
      <h1 className="text-2xl font-semibold">检索调试</h1>
      <form onSubmit={(e) => void search(e)} className="space-y-3 border-b pb-5">
        <label className="block text-sm">
          查询
          <textarea
            className="mt-1 block min-h-24 w-full rounded-md border bg-background p-3"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            required
            maxLength={1000}
          />
        </label>
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-sm">
            Agent
            <select
              className="mt-1 block h-9 rounded-md border bg-background px-3"
              value={agent}
              onChange={(e) => setAgent(e.target.value)}
            >
              <option value="imd">遗传代谢</option>
              <option value="general">General</option>
            </select>
          </label>
          <label className="min-w-0 flex-1 text-sm">
            疾病标签
            <input
              className="mt-1 block h-9 w-full rounded-md border bg-background px-3"
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              maxLength={500}
            />
          </label>
          <Button disabled={busy || !query.trim()} type="submit">
            <Search />
            {busy ? "检索中" : "检索"}
          </Button>
        </div>
      </form>
      {error ? (
        <p role="alert" className="text-destructive">
          {error}
        </p>
      ) : null}
      {result ? (
        <>
          <div className="flex flex-wrap gap-4 text-sm text-muted-foreground">
            <span>候选扫描 {result.diagnostics.scanned_chunks}</span>
            <span>有效向量 {result.diagnostics.usable_vector_chunks}</span>
            <span>耗时 {result.duration_ms} ms</span>
            <span>返回 {result.results.length} 条</span>
          </div>
          {result.diagnostics.degraded_reason ? (
            <p className="text-sm text-destructive">
              {result.diagnostics.degraded_reason === "query_embedding_unavailable"
                ? "查询向量不可用，已降级为关键词检索"
                : "候选文档缺少有效向量"}
            </p>
          ) : null}
          {!result.results.length ? <p className="text-muted-foreground">没有相关证据</p> : null}
          {result.results.map((h, i) => (
            <article key={h.chunk_id} className="space-y-2 border-b pb-5">
              <h2 className="text-base font-semibold">
                {i + 1}. {h.title}
              </h2>
              <Link className="text-sm underline" href={`/knowledge/${h.document_id}`}>
                {h.document_title} · {h.section_label ?? "正文"}
              </Link>
              <p className="text-xs text-muted-foreground">
                关键词排名 {h.keyword_rank ?? "未入选"} · 向量排名 {h.vector_rank ?? "未入选"} ·
                相似度 {h.vector_score}
              </p>
              <p className="text-sm leading-6 break-words whitespace-pre-wrap">{h.content}</p>
              {h.adjacent.length ? (
                <details className="text-sm">
                  <summary className="cursor-pointer">相邻片段</summary>
                  {h.adjacent.map((a) => (
                    <p className="mt-2 break-words whitespace-pre-wrap" key={a.chunk_id}>
                      {a.content}
                    </p>
                  ))}
                </details>
              ) : null}
            </article>
          ))}
        </>
      ) : null}
    </div>
  );
}
