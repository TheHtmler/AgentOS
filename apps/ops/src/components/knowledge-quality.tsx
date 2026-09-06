"use client";

import { useEffect, useState } from "react";
import { Download, FileScan, RefreshCw, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { opsJson } from "@/lib/ops-fetch";

type Quality = {
  chunk_count: number;
  embedded_chunks: number;
  import_status: string;
  import_stage: string | null;
  import_error: string | null;
  source_available: boolean;
  import_progress_done: number | null;
  import_progress_total: number | null;
  quality: { pages?: Array<{ page: number; status: string; error?: string }> };
};
const stages: Record<string, string> = {
  extracting: "解析中",
  embedding: "向量化中",
  persisting: "保存中",
  complete: "处理完成",
};
const pageStates: Record<string, string> = {
  vision: "转录完成",
  fallback: "文本层回退，需复核",
  missing: "缺失",
};

export function KnowledgeQuality({ documentId }: { documentId: string }) {
  const [data, setData] = useState<Quality | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const detail = await opsJson<Quality>(`/api/ops/knowledge/documents/${documentId}`);
        if (cancelled) return;
        setData(detail);
        if (detail.import_status === "processing") timer = setTimeout(() => void load(), 2500);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "加载失败");
      }
    }
    void load();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [documentId, revision]);
  async function rebuild(mode: string) {
    setBusy(true);
    setError("");
    try {
      await opsJson(`/api/ops/knowledge/documents/${documentId}/rebuild`, {
        method: "POST",
        body: JSON.stringify({ mode }),
      });
      setRevision((v) => v + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "提交失败");
    } finally {
      setBusy(false);
    }
  }
  const disabled = busy || data?.import_status === "processing";
  return (
    <section className="space-y-3 border-y py-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="text-sm">
          有效向量{" "}
          <strong>
            {data?.embedded_chunks ?? 0}/{data?.chunk_count ?? 0}
          </strong>
          <span className="ml-3 text-muted-foreground">
            {data?.import_status === "failed"
              ? "处理失败"
              : (stages[data?.import_stage ?? ""] ?? "旧版资料")}
          </span>
          {data?.import_status === "processing" && data.import_progress_total ? (
            <span className="ml-2">
              {data.import_progress_done ?? 0}/{data.import_progress_total}
            </span>
          ) : null}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={disabled}
            onClick={() => void rebuild("vectors")}
          >
            <RefreshCw />
            重建向量
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={disabled || !data?.source_available}
            onClick={() => void rebuild("reparse")}
          >
            <FileScan />
            重新解析
          </Button>
          {data?.import_status === "failed" ? (
            <Button
              variant="outline"
              size="sm"
              disabled={disabled}
              onClick={() => void rebuild("retry")}
            >
              <RotateCcw />
              重试任务
            </Button>
          ) : null}
          {data?.source_available ? (
            <Button asChild variant="ghost" size="icon-sm" title="下载原件">
              <a href={`/api/ops/knowledge/documents/${documentId}/source`}>
                <Download />
                <span className="sr-only">下载原件</span>
              </a>
            </Button>
          ) : null}
        </div>
      </div>
      {error || data?.import_error ? (
        <p role="alert" className="text-sm text-destructive">
          {error || data?.import_error}
        </p>
      ) : null}
      {data?.quality.pages?.length ? (
        <details className="text-sm">
          <summary className="cursor-pointer">页级质量 · {data.quality.pages.length} 页</summary>
          <div className="mt-2 max-h-64 overflow-auto">
            {data.quality.pages.map((p) => (
              <p key={p.page} className="border-b py-2">
                第 {p.page} 页 · {pageStates[p.status] ?? p.status}
                {p.error ? ` · ${p.error}` : ""}
              </p>
            ))}
          </div>
        </details>
      ) : null}
    </section>
  );
}
