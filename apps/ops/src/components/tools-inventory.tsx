"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { PageHeader } from "@/components/page-header";
import { Skeleton } from "@/components/skeleton";
import { POLICY_ACTION_LABELS, TOOL_DOMAIN_LABELS, TOOL_RISK_LABELS, labelOf } from "@/lib/labels";
import { opsJson } from "@/lib/ops-fetch";

type OpsTool = {
  name: string;
  domain: string;
  risk: string;
  default_action: string;
  effective_action: string;
  enabled: boolean;
  description: string;
  source: "builtin" | "mcp";
};

type OpsToolsResponse = {
  mcp_enabled: boolean;
  tools: OpsTool[];
};

export function ToolsInventory({
  title,
  lead,
  source,
}: {
  title: string;
  lead: string;
  source: "builtin" | "mcp";
}) {
  const [data, setData] = useState<OpsToolsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        setData(await opsJson<OpsToolsResponse>("/api/ops/tools"));
      } catch (err) {
        setError(err instanceof Error ? err.message : "加载失败");
      }
    })();
  }, []);

  const tools = useMemo(
    () => (data?.tools ?? []).filter((tool) => tool.source === source),
    [data, source],
  );

  return (
    <div className="stack">
      <PageHeader title={title} lead={lead} />

      {source === "mcp" && data ? (
        <section className="callout">
          <h2>当前接入状态</h2>
          <p>
            外部 MCP {data.mcp_enabled ? "已开启" : "未开启"}
            。本页只展示登记清单，不提供服务器配置。
          </p>
        </section>
      ) : null}

      {error ? <p className="error">{error}</p> : null}
      {!data && !error ? <Skeleton /> : null}

      {data && tools.length === 0 ? (
        <section className="panel">
          <p className="muted" style={{ margin: 0 }}>
            {source === "mcp"
              ? "还没有登记外部 MCP 工具。运行时允许名单为空时，这里会保持空白。"
              : "没有内建技能。"}
          </p>
        </section>
      ) : null}

      {tools.length > 0 ? (
        <div className="doc-list always">
          {tools.map((tool) => (
            <Link
              key={tool.name}
              href={`/${source === "builtin" ? "skills" : "mcp"}/${encodeURIComponent(tool.name)}`}
              className="doc-card tool-inventory-link"
            >
              <div className="doc-card__title">{tool.name}</div>
              <p style={{ margin: 0 }}>{tool.description}</p>
              <div className="doc-card__meta">
                <span>{labelOf(TOOL_DOMAIN_LABELS, tool.domain)}</span>
                <span>{labelOf(TOOL_RISK_LABELS, tool.risk)}</span>
                <span className={`badge badge--${tool.enabled ? "completed" : "cancelled"}`}>
                  {tool.enabled ? "已启用" : "已关闭"}
                </span>
                <span>
                  默认 {labelOf(POLICY_ACTION_LABELS, tool.default_action)} · 当前{" "}
                  {labelOf(POLICY_ACTION_LABELS, tool.effective_action)}
                </span>
              </div>
              <span className="tool-inventory-link__action">查看输入/输出定义 →</span>
            </Link>
          ))}
        </div>
      ) : null}
    </div>
  );
}
