"use client";

import {
  BookOpen,
  Calculator,
  ChartColumn,
  Clock,
  ExternalLink,
  FileText,
  Folder,
  Link2,
  Search,
  Terminal,
  Wrench,
} from "lucide-react";

export function ToolIcon({ toolName, className }: { toolName: string; className?: string }) {
  const cls = className ?? "size-4";

  if (toolName === "web_search") {
    return <Search className={cls} aria-hidden="true" />;
  }
  if (toolName === "fetch_url") {
    return <Link2 className={cls} aria-hidden="true" />;
  }
  if (toolName === "read_artifact") {
    return <FileText className={cls} aria-hidden="true" />;
  }
  if (toolName === "calculate") {
    return <Calculator className={cls} aria-hidden="true" />;
  }
  if (toolName === "time_diff") {
    return <Clock className={cls} aria-hidden="true" />;
  }
  if (toolName === "growth_assess") {
    return <ChartColumn className={cls} aria-hidden="true" />;
  }
  if (toolName === "knowledge_search") {
    return <BookOpen className={cls} aria-hidden="true" />;
  }
  if (toolName === "sandbox_exec") {
    return <Terminal className={cls} aria-hidden="true" />;
  }
  if (toolName.startsWith("case_")) {
    return <Folder className={cls} aria-hidden="true" />;
  }
  if (toolName.startsWith("mcp_")) {
    return <ExternalLink className={cls} aria-hidden="true" />;
  }
  return <Wrench className={cls} aria-hidden="true" />;
}
