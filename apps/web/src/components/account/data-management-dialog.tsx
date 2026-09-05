"use client";

import { Database, Trash2 } from "lucide-react";
import { useCallback, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";

type Row = { id: string; title?: string; content?: string; kind: string; created_at: string };

export function DataManagementDialog() {
  const [open, setOpen] = useState(false);
  const [memories, setMemories] = useState<Row[]>([]);
  const [artifacts, setArtifacts] = useState<Row[]>([]);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [memoryResponse, artifactResponse] = await Promise.all([
      fetch("/api/me/memories", { cache: "no-store" }),
      fetch("/api/me/artifacts", { cache: "no-store" }),
    ]);
    if (memoryResponse.ok)
      setMemories(((await memoryResponse.json()) as { memories: Row[] }).memories);
    if (artifactResponse.ok)
      setArtifacts(((await artifactResponse.json()) as { artifacts: Row[] }).artifacts);
  }, []);

  const handleOpenChange = useCallback(
    (nextOpen: boolean) => {
      setOpen(nextOpen);
      if (nextOpen) void load();
    },
    [load],
  );

  async function remove(kind: "memories" | "artifacts", id: string) {
    if (!window.confirm("删除后无法恢复，确定继续吗？")) return;
    setBusy(id);
    try {
      const response = await fetch(`/api/me/${kind}/${id}`, { method: "DELETE" });
      if (response.ok) await load();
    } finally {
      setBusy(null);
    }
  }

  const renderRows = (kind: "memories" | "artifacts", rows: Row[]) =>
    rows.length === 0 ? (
      <p className="text-sm text-muted-foreground">暂无数据</p>
    ) : (
      <div className="grid max-h-48 gap-2 overflow-y-auto">
        {rows.map((row) => (
          <div key={row.id} className="flex min-w-0 items-center gap-2 border-b pb-2 text-sm">
            <span className="min-w-0 flex-1 truncate" title={row.title ?? row.content}>
              {row.title ?? row.content}
            </span>
            <Button
              variant="ghost"
              size="icon"
              aria-label="删除"
              disabled={busy === row.id}
              onClick={() => void remove(kind, row.id)}
            >
              <Trash2 className="size-4" />
            </Button>
          </div>
        ))}
      </div>
    );

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <button type="button" aria-label="数据管理" title="数据管理">
          <Database className="size-4" />
        </button>
      </DialogTrigger>
      <DialogContent className="max-h-[80vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>数据管理</DialogTitle>
          <DialogDescription>
            可删除自己的长期记忆和附件。删除附件会同时移除原始文件。
          </DialogDescription>
        </DialogHeader>
        <section>
          <h3 className="mb-2 text-sm font-semibold">长期记忆</h3>
          {renderRows("memories", memories)}
        </section>
        <section>
          <h3 className="mb-2 text-sm font-semibold">附件与生成内容</h3>
          {renderRows("artifacts", artifacts)}
        </section>
      </DialogContent>
    </Dialog>
  );
}
