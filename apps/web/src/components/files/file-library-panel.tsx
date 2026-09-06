"use client";

import {
  Download,
  Eye,
  File,
  FileImage,
  FileSpreadsheet,
  FileText,
  FolderOpen,
  LoaderCircle,
  Search,
  Trash2,
  Upload,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";

type LibraryFile = {
  id: string;
  title: string;
  original_filename: string;
  mime_type: string;
  byte_size: number | null;
  thread_id: string | null;
  thread_agent_id: string | null;
  created_at: string;
};

type PreviewState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "image" | "pdf"; objectUrl: string }
  | { kind: "unsupported" };

type FileLibraryPanelProps = {
  agentId: string | null;
  onOpenThread: (threadId: string, agentId?: string) => void;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isLibraryFile(value: unknown): value is LibraryFile {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.title === "string" &&
    typeof value.original_filename === "string" &&
    typeof value.mime_type === "string" &&
    (typeof value.byte_size === "number" || value.byte_size === null) &&
    (typeof value.thread_id === "string" || value.thread_id === null) &&
    (typeof value.thread_agent_id === "string" || value.thread_agent_id === null) &&
    typeof value.created_at === "string"
  );
}

function parseFiles(value: unknown): LibraryFile[] | null {
  if (!isRecord(value) || !Array.isArray(value.files)) return null;
  return value.files.every(isLibraryFile) ? value.files : null;
}

function formatSize(bytes: number | null): string {
  if (bytes === null) return "-";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "-";
  return date.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric", year: "numeric" });
}

function fileKind(file: LibraryFile): "image" | "pdf" | "other" {
  if (file.mime_type.startsWith("image/")) return "image";
  if (file.mime_type === "application/pdf") return "pdf";
  return "other";
}

function FileTypeIcon({ file }: { file: LibraryFile }) {
  if (file.mime_type.startsWith("image/")) return <FileImage className="size-5 text-sky-600" />;
  if (file.mime_type === "application/pdf") return <FileText className="size-5 text-rose-600" />;
  if (file.original_filename.endsWith(".csv"))
    return <FileSpreadsheet className="size-5 text-emerald-600" />;
  return <File className="size-5 text-muted-foreground" />;
}

function PreviewContent({ file }: { file: LibraryFile }) {
  const [state, setState] = useState<PreviewState>({ kind: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | null = null;

    void fetch(`/api/uploads/${file.id}/content`, { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("文件暂时无法读取");
        const type = (response.headers.get("content-type") ?? "").split(";", 1)[0].trim();
        if (!type.startsWith("image/") && type !== "application/pdf") {
          setState({ kind: "unsupported" });
          return;
        }
        objectUrl = URL.createObjectURL(await response.blob());
        setState({ kind: type.startsWith("image/") ? "image" : "pdf", objectUrl });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setState({
            kind: "error",
            message: error instanceof Error ? error.message : "文件暂时无法读取",
          });
        }
      });

    return () => {
      controller.abort();
      if (objectUrl !== null) URL.revokeObjectURL(objectUrl);
    };
  }, [file]);

  return (
    <div className="min-h-0 flex-1 overflow-auto bg-muted/30 p-4">
      {state.kind === "loading" ? (
        <div className="grid h-full place-items-center text-sm text-muted-foreground">
          正在加载预览…
        </div>
      ) : null}
      {state.kind === "error" ? (
        <div className="grid h-full place-items-center text-sm text-destructive">
          {state.message}
        </div>
      ) : null}
      {state.kind === "unsupported" ? (
        <div className="grid h-full place-items-center text-sm text-muted-foreground">
          该文件格式暂不支持在线预览，请下载后查看。
        </div>
      ) : null}
      {state.kind === "image" ? (
        <img
          src={state.objectUrl}
          alt={file.title}
          className="mx-auto max-h-full max-w-full object-contain"
        />
      ) : null}
      {state.kind === "pdf" ? (
        <iframe
          src={state.objectUrl}
          title={file.title}
          sandbox=""
          className="h-full w-full bg-white"
        />
      ) : null}
    </div>
  );
}

function PreviewDialog({
  file,
  onOpenChange,
}: {
  file: LibraryFile | null;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={file !== null} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[min(80vh,56rem)] max-w-5xl flex-col p-0">
        <DialogHeader className="border-b px-5 py-4">
          <DialogTitle className="truncate pr-6">{file?.title ?? "文件预览"}</DialogTitle>
        </DialogHeader>
        {file !== null ? <PreviewContent key={file.id} file={file} /> : null}
      </DialogContent>
    </Dialog>
  );
}

export function FileLibraryPanel({ agentId, onOpenThread }: FileLibraryPanelProps) {
  const [files, setFiles] = useState<LibraryFile[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState<"all" | "image" | "pdf">("all");
  const [previewFile, setPreviewFile] = useState<LibraryFile | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<LibraryFile | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const loadFiles = useCallback(async () => {
    setIsLoading(true);
    try {
      const response = await fetch("/api/me/files", { cache: "no-store" });
      if (!response.ok) throw new Error("无法读取文件库。");
      const parsed = parseFiles((await response.json()) as unknown);
      if (parsed === null) throw new Error("文件库数据格式无效。");
      setFiles(parsed);
      setError(null);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "无法读取文件库。");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadFiles(), 0);
    return () => window.clearTimeout(timer);
  }, [loadFiles]);

  const visibleFiles = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    return files.filter((file) => {
      const kind = fileKind(file);
      return (
        (typeFilter === "all" || typeFilter === kind) &&
        (!normalized ||
          `${file.title} ${file.original_filename}`.toLocaleLowerCase().includes(normalized))
      );
    });
  }, [files, query, typeFilter]);

  async function uploadFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file === undefined) return;

    setIsUploading(true);
    try {
      const threadResponse = await fetch("/api/threads", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(agentId === null ? {} : { agent_id: agentId }),
      });
      if (!threadResponse.ok) throw new Error("无法为文件创建会话。");
      const thread = (await threadResponse.json()) as { id?: string };
      if (typeof thread.id !== "string") throw new Error("会话创建响应无效。");

      const formData = new FormData();
      formData.append("thread_id", thread.id);
      formData.append("file", file);
      const uploadResponse = await fetch("/api/uploads", { method: "POST", body: formData });
      if (!uploadResponse.ok) {
        const detail = (await uploadResponse.json().catch(() => null)) as {
          detail?: string;
        } | null;
        throw new Error(detail?.detail ?? "文件上传失败。");
      }
      await loadFiles();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "文件上传失败。");
    } finally {
      setIsUploading(false);
    }
  }

  async function deleteFile() {
    if (deleteTarget === null) return;
    setIsDeleting(true);
    try {
      const response = await fetch(`/api/me/artifacts/${deleteTarget.id}`, { method: "DELETE" });
      if (!response.ok) throw new Error("删除失败，请稍后重试。");
      setDeleteTarget(null);
      await loadFiles();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "删除失败，请稍后重试。");
    } finally {
      setIsDeleting(false);
    }
  }

  return (
    <section className="mx-auto flex h-full min-h-0 w-full max-w-6xl flex-col px-4 py-5 sm:px-6 lg:px-8">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b pb-4">
        <div>
          <h1 className="text-xl font-semibold text-foreground">文件库</h1>
          <p className="mt-1 text-sm text-muted-foreground">管理上传到会话的原始文件。</p>
        </div>
        <input
          ref={fileInputRef}
          type="file"
          className="sr-only"
          accept=".pdf,.png,.jpg,.jpeg,.webp,application/pdf,image/png,image/jpeg,image/webp"
          onChange={(event) => void uploadFile(event)}
        />
        <Button type="button" disabled={isUploading} onClick={() => fileInputRef.current?.click()}>
          {isUploading ? (
            <LoaderCircle className="size-4 animate-spin" />
          ) : (
            <Upload className="size-4" />
          )}
          上传文件
        </Button>
      </header>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <label className="relative min-w-52 flex-1 sm:max-w-sm">
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground"
          />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            className="pl-9"
            placeholder="搜索文件"
          />
        </label>
        {(["all", "image", "pdf"] as const).map((filter) => (
          <Button
            key={filter}
            type="button"
            variant={typeFilter === filter ? "secondary" : "ghost"}
            size="sm"
            onClick={() => setTypeFilter(filter)}
          >
            {filter === "all" ? "全部" : filter === "image" ? "图片" : "PDF"}
          </Button>
        ))}
      </div>

      {error !== null ? (
        <p role="alert" className="mt-4 text-sm text-destructive">
          {error}
        </p>
      ) : null}
      <div className="mt-4 min-h-0 flex-1 overflow-auto rounded-md border bg-card">
        {isLoading ? (
          <div className="grid h-40 place-items-center text-sm text-muted-foreground">
            正在加载文件…
          </div>
        ) : null}
        {!isLoading && visibleFiles.length === 0 ? (
          <div className="grid h-56 place-items-center p-6 text-center">
            <div>
              <FolderOpen aria-hidden="true" className="mx-auto size-8 text-muted-foreground" />
              <p className="mt-3 text-sm font-medium text-foreground">
                {files.length === 0 ? "还没有文件" : "没有匹配的文件"}
              </p>
              <p className="mt-1 text-sm text-muted-foreground">
                上传的文件会保存在这里，之后可随时预览和下载。
              </p>
            </div>
          </div>
        ) : null}
        {!isLoading && visibleFiles.length > 0 ? (
          <ul className="divide-y" aria-label="文件列表">
            {visibleFiles.map((file) => (
              <li key={file.id} className="flex min-w-0 items-center gap-3 px-4 py-3">
                <div className="grid size-9 shrink-0 place-items-center rounded-md bg-muted">
                  <FileTypeIcon file={file} />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-foreground" title={file.title}>
                    {file.title}
                  </p>
                  <p className="mt-0.5 truncate text-xs text-muted-foreground">
                    {file.original_filename} · {formatSize(file.byte_size)} ·{" "}
                    {formatDate(file.created_at)}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    aria-label="预览文件"
                    title="预览"
                    onClick={() => setPreviewFile(file)}
                  >
                    <Eye className="size-4" />
                  </Button>
                  <a
                    href={`/api/uploads/${file.id}/content`}
                    download
                    aria-label="下载文件"
                    title="下载"
                    className="inline-flex size-9 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
                  >
                    <Download className="size-4" />
                  </a>
                  {file.thread_id !== null ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="hidden sm:inline-flex"
                      onClick={() =>
                        onOpenThread(file.thread_id!, file.thread_agent_id ?? undefined)
                      }
                    >
                      会话
                    </Button>
                  ) : null}
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    aria-label="删除文件"
                    title="删除"
                    className="text-destructive hover:text-destructive"
                    onClick={() => setDeleteTarget(file)}
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        ) : null}
      </div>

      <PreviewDialog
        file={previewFile}
        onOpenChange={(open) => {
          if (!open) setPreviewFile(null);
        }}
      />
      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除文件？</AlertDialogTitle>
            <AlertDialogDescription>
              “{deleteTarget?.title}
              ”将从文件库和原始文件存储中永久删除，已引用它的历史会话不会被删除。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isDeleting}>取消</AlertDialogCancel>
            <AlertDialogAction
              disabled={isDeleting}
              onClick={(event) => {
                event.preventDefault();
                void deleteFile();
              }}
            >
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
