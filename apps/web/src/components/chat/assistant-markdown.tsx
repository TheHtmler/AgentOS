"use client";

import { Children, isValidElement, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

function collectText(node: ReactNode): string {
  if (node === null || node === undefined || typeof node === "boolean") {
    return "";
  }
  if (typeof node === "string" || typeof node === "number") {
    return String(node);
  }
  if (Array.isArray(node)) {
    return node.map(collectText).join("");
  }
  if (isValidElement<{ children?: ReactNode }>(node)) {
    return collectText(node.props.children);
  }
  return "";
}

function languageFromClassName(className: string | undefined): string | null {
  if (!className) {
    return null;
  }
  const match = /language-([a-z0-9_+-]+)/i.exec(className);
  return match?.[1] ?? null;
}

function CodeBlock({ children }: { children: ReactNode }) {
  const [copied, setCopied] = useState(false);
  const codeElement = Children.toArray(children).find((child) => isValidElement(child));
  const className =
    isValidElement<{ className?: string }>(codeElement) &&
    typeof codeElement.props.className === "string"
      ? codeElement.props.className
      : undefined;
  const language = languageFromClassName(className);
  const codeText = collectText(children).replace(/\n$/, "");

  async function copyCode() {
    if (!codeText) {
      return;
    }
    try {
      await navigator.clipboard.writeText(codeText);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1_500);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="agentos-code-block">
      <div className="agentos-code-block-toolbar">
        <span className="agentos-code-block-lang">{language ?? "code"}</span>
        <button
          type="button"
          onClick={() => void copyCode()}
          className="agentos-code-block-copy"
          aria-label="复制代码"
        >
          {copied ? "已复制" : "复制"}
        </button>
      </div>
      <pre>{children}</pre>
    </div>
  );
}

export function AssistantMarkdown({ content }: { content: string }) {
  return (
    <div className="agentos-md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeSanitize]}
        components={{
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          ),
          pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
