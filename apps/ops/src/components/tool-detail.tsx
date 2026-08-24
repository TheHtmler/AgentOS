"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { PageHeader } from "@/components/page-header";
import { Skeleton } from "@/components/skeleton";
import { POLICY_ACTION_LABELS, TOOL_DOMAIN_LABELS, TOOL_RISK_LABELS, labelOf } from "@/lib/labels";
import { opsJson } from "@/lib/ops-fetch";

type JsonSchema = Record<string, unknown>;

type OpsToolDetail = {
  name: string;
  domain: string;
  risk: string;
  default_action: string;
  effective_action: string;
  enabled: boolean;
  description: string;
  source: "builtin" | "mcp";
  input_schema: JsonSchema;
  output_schema: JsonSchema;
  output_description: string;
  output_transport: string;
};

const sourceLabels: Record<OpsToolDetail["source"], string> = {
  builtin: "内建工具",
  mcp: "MCP 工具",
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asStringList(value: unknown): string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string") ? value : [];
}

function schemaType(schema: Record<string, unknown>): string {
  const type = schema.type;
  if (typeof type === "string") return type;
  if (Array.isArray(type)) return type.filter((item) => typeof item === "string").join(" | ");

  const alternatives = schema.anyOf ?? schema.oneOf;
  if (Array.isArray(alternatives)) {
    const types = alternatives
      .map((item) => (asRecord(item) ? schemaType(item) : ""))
      .filter(Boolean);
    if (types.length > 0) return types.join(" | ");
  }
  if (schema.properties) return "object";
  if (schema.items) return "array";
  return "any";
}

function displayValue(value: unknown): string {
  if (value === undefined) return "—";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function SchemaFields({ schema }: { schema: JsonSchema }) {
  const properties = asRecord(schema.properties);
  const required = new Set(asStringList(schema.required));

  if (!properties || Object.keys(properties).length === 0) {
    return <p className="muted">该工具没有可展开的本地字段定义，请查看下方原始 Schema。</p>;
  }

  return (
    <div className="schema-field-list">
      {Object.entries(properties).map(([name, value]) => {
        const field = asRecord(value) ?? {};
        return (
          <div key={name} className="schema-field">
            <div className="schema-field__head">
              <code>{name}</code>
              <span className="schema-field__type">{schemaType(field)}</span>
              {required.has(name) ? <span className="schema-field__required">必填</span> : null}
            </div>
            <div className="schema-field__meta">
              <span>默认值：{displayValue(field.default)}</span>
              {typeof field.description === "string" ? <span>{field.description}</span> : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function SchemaPanel({
  title,
  schema,
  description,
}: {
  title: string;
  schema: JsonSchema;
  description: string;
}) {
  return (
    <section className="panel stack schema-panel">
      <div>
        <h2 className="section-title">{title}</h2>
        <p className="muted schema-panel__description">{description}</p>
      </div>
      <SchemaFields schema={schema} />
      <details className="schema-raw">
        <summary>查看原始 JSON Schema</summary>
        <pre className="chunk-body">{JSON.stringify(schema, null, 2)}</pre>
      </details>
    </section>
  );
}

export function ToolDetail() {
  const params = useParams<{ toolName: string }>();
  const [detail, setDetail] = useState<OpsToolDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!params.toolName) return;
    void (async () => {
      try {
        setDetail(
          await opsJson<OpsToolDetail>(`/api/ops/tools/${encodeURIComponent(params.toolName)}`),
        );
      } catch (err) {
        setError(err instanceof Error ? err.message : "加载失败");
      }
    })();
  }, [params.toolName]);

  if (error) {
    return (
      <div className="stack">
        <PageHeader
          title="工具定义"
          actions={
            <Link className="btn" href="/tools">
              返回工具
            </Link>
          }
        />
        <p className="error">{error}</p>
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="stack">
        <PageHeader
          title="工具定义"
          actions={
            <Link className="btn" href="/tools">
              返回工具
            </Link>
          }
        />
        <Skeleton />
      </div>
    );
  }

  return (
    <div className="stack">
      <PageHeader
        title={detail.name}
        lead={detail.description}
        actions={
          <Link className="btn" href="/tools">
            返回工具
          </Link>
        }
      />

      <section className="panel stack">
        <div className="section-head">
          <h2 className="section-title">工具状态</h2>
          <span className={`badge badge--${detail.enabled ? "completed" : "cancelled"}`}>
            {detail.enabled ? "已启用" : "已关闭"}
          </span>
        </div>
        <div className="tool-detail-meta">
          <div>
            <span className="muted">来源</span>
            <strong>{sourceLabels[detail.source]}</strong>
          </div>
          <div>
            <span className="muted">能力域</span>
            <strong>{labelOf(TOOL_DOMAIN_LABELS, detail.domain)}</strong>
          </div>
          <div>
            <span className="muted">风险级别</span>
            <strong>{labelOf(TOOL_RISK_LABELS, detail.risk)}</strong>
          </div>
          <div>
            <span className="muted">当前策略</span>
            <strong>{labelOf(POLICY_ACTION_LABELS, detail.effective_action)}</strong>
          </div>
        </div>
      </section>

      <div className="tool-schema-grid">
        <SchemaPanel
          title="输入参数 Schema"
          schema={detail.input_schema}
          description="模型调用工具时提交的参数。字段类型、必填状态和默认值来自运行时定义。"
        />
        <SchemaPanel
          title="输出参数 Schema"
          schema={detail.output_schema}
          description={`${detail.output_description} 运行时传输：${detail.output_transport === "json_string" ? "JSON 字符串" : detail.output_transport}。`}
        />
      </div>
    </div>
  );
}
