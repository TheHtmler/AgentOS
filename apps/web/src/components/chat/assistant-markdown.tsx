"use client";

import {
  Children,
  isValidElement,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
  type UIEvent,
  type WheelEvent,
} from "react";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

export const PAUSE_CHAT_AUTOSCROLL_EVENT = "agentos:pause-chat-autoscroll";

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

function requestPauseChatAutoScroll() {
  window.dispatchEvent(new Event(PAUSE_CHAT_AUTOSCROLL_EVENT));
}

function CodeBlock({ children }: { children: ReactNode }) {
  const [copied, setCopied] = useState(false);
  const preRef = useRef<HTMLPreElement | null>(null);
  // While streaming, follow the growing code unless the user scrolls up inside the block.
  const stickToBottomRef = useRef(true);
  const savedScrollTopRef = useRef(0);

  const codeElement = Children.toArray(children).find((child) => isValidElement(child));
  const className =
    isValidElement<{ className?: string }>(codeElement) &&
    typeof codeElement.props.className === "string"
      ? codeElement.props.className
      : undefined;
  const language = languageFromClassName(className);
  const codeText = collectText(children).replace(/\n$/, "");

  useLayoutEffect(() => {
    const pre = preRef.current;
    if (pre === null) {
      return;
    }

    // Token updates often reset native scrollTop; restore follow or the user's offset.
    if (stickToBottomRef.current) {
      pre.scrollTop = pre.scrollHeight;
    } else {
      pre.scrollTop = savedScrollTopRef.current;
    }
  });

  function handlePreScroll(event: UIEvent<HTMLPreElement>) {
    const pre = event.currentTarget;
    savedScrollTopRef.current = pre.scrollTop;
    const distanceFromBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight;
    stickToBottomRef.current = distanceFromBottom < 32;
  }

  function handlePreWheel(event: WheelEvent<HTMLPreElement>) {
    // Keep wheel inside the code pane; also stop the chat viewport from yanking back.
    event.stopPropagation();
    requestPauseChatAutoScroll();
    if (event.deltaY < 0) {
      stickToBottomRef.current = false;
    }
  }

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
    <div
      className="agentos-code-block"
      onPointerDown={requestPauseChatAutoScroll}
    >
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
      <pre
        ref={preRef}
        onScroll={handlePreScroll}
        onWheel={handlePreWheel}
        onTouchMove={requestPauseChatAutoScroll}
      >
        {children}
      </pre>
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
