"use client";

import {
  Children,
  isValidElement,
  memo,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
  type UIEvent,
} from "react";
import { createPortal } from "react-dom";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

function subscribeNoop() {
  return () => {};
}

function useIsClient() {
  return useSyncExternalStore(
    subscribeNoop,
    () => true,
    () => false,
  );
}

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

function extractCodeParts(children: ReactNode): { language: string | null; codeText: string } {
  const codeElement = Children.toArray(children).find((child) => isValidElement(child));
  const className =
    isValidElement<{ className?: string }>(codeElement) &&
    typeof codeElement.props.className === "string"
      ? codeElement.props.className
      : undefined;
  return {
    language: languageFromClassName(className),
    codeText: collectText(children).replace(/\n$/, ""),
  };
}

let mermaidRenderSeq = 0;

function MermaidZoomOverlay({ svg, onClose }: { svg: string; onClose: () => void }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState(1);
  const [naturalWidth, setNaturalWidth] = useState<number | null>(null);

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  // mermaid sets max-width:100% on the svg; measure the viewBox once and then
  // drive an explicit pixel width so zoom actually enlarges past the viewport.
  useEffect(() => {
    const svgEl = scrollRef.current?.querySelector("svg");
    const width = svgEl?.viewBox?.baseVal?.width;
    if (width && width > 0) {
      setNaturalWidth(width);
    }
  }, []);

  useEffect(() => {
    const svgEl = scrollRef.current?.querySelector("svg");
    if (!svgEl || naturalWidth === null) {
      return;
    }
    svgEl.style.maxWidth = "none";
    svgEl.style.width = `${Math.round(naturalWidth * zoom)}px`;
    svgEl.style.height = "auto";
  }, [zoom, naturalWidth]);

  return (
    <div
      className="agentos-mermaid-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="放大查看图表"
    >
      <div className="agentos-mermaid-overlay-toolbar">
        <div className="agentos-mermaid-overlay-zoom">
          <button
            type="button"
            className="agentos-code-block-copy"
            onClick={() => setZoom((value) => Math.max(0.5, value / 1.25))}
            aria-label="缩小"
          >
            −
          </button>
          <span>{Math.round(zoom * 100)}%</span>
          <button
            type="button"
            className="agentos-code-block-copy"
            onClick={() => setZoom((value) => Math.min(4, value * 1.25))}
            aria-label="放大"
          >
            +
          </button>
          <button type="button" className="agentos-code-block-copy" onClick={() => setZoom(1)}>
            重置
          </button>
        </div>
        <button
          type="button"
          className="agentos-code-block-copy"
          onClick={onClose}
          aria-label="关闭"
        >
          ✕
        </button>
      </div>
      <div
        ref={scrollRef}
        className="agentos-mermaid-overlay-scroll"
        onClick={(event) => {
          if (event.target === event.currentTarget) {
            onClose();
          }
        }}
      >
        <div className="agentos-mermaid-overlay-canvas" dangerouslySetInnerHTML={{ __html: svg }} />
      </div>
    </div>
  );
}

function MermaidBlock({ codeText }: { codeText: string }) {
  // Keep the last successfully rendered diagram on screen; while the fence is
  // still streaming in, parses fail and the source view shows instead.
  const [svg, setSvg] = useState<string | null>(null);
  const [showCode, setShowCode] = useState(false);
  const [zoomOpen, setZoomOpen] = useState(false);
  const isClient = useIsClient();

  useEffect(() => {
    let cancelled = false;
    const renderId = `agentos-mmd-${(mermaidRenderSeq += 1)}`;
    const timer = window.setTimeout(() => {
      void (async () => {
        try {
          const { default: mermaid } = await import("mermaid");
          mermaid.initialize({
            startOnLoad: false,
            securityLevel: "strict",
            theme: document.documentElement.dataset["theme"] === "dark" ? "dark" : "default",
          });
          const result = await mermaid.render(renderId, codeText);
          if (!cancelled) {
            setSvg(result.svg);
          }
        } catch {
          // Partial source mid-stream is unparsable — keep the previous render.
        } finally {
          // mermaid appends its error diagram to <body> on parse failure.
          document.getElementById(renderId)?.remove();
          document.getElementById(`d${renderId}`)?.remove();
        }
      })();
    }, 300);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [codeText]);

  return (
    <div className="agentos-code-block">
      <div className="agentos-code-block-toolbar">
        <span className="agentos-code-block-lang">mermaid</span>
        {svg !== null ? (
          <span className="agentos-mermaid-actions">
            <button
              type="button"
              onClick={() => setZoomOpen(true)}
              className="agentos-code-block-copy"
            >
              放大
            </button>
            <button
              type="button"
              onClick={() => setShowCode((value) => !value)}
              className="agentos-code-block-copy"
            >
              {showCode ? "图表" : "代码"}
            </button>
          </span>
        ) : null}
      </div>
      {showCode || svg === null ? (
        <div className="agentos-code-block-scroll">
          <pre>
            <code>{codeText}</code>
          </pre>
        </div>
      ) : (
        <div
          className="agentos-mermaid-canvas"
          title="点击放大"
          onClick={() => setZoomOpen(true)}
          dangerouslySetInnerHTML={{ __html: svg }}
        />
      )}
      {zoomOpen && isClient && svg !== null
        ? createPortal(
            <MermaidZoomOverlay svg={svg} onClose={() => setZoomOpen(false)} />,
            document.body,
          )
        : null}
    </div>
  );
}

function CodeBlock({ children }: { children: ReactNode }) {
  const [copied, setCopied] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const stickToBottomRef = useRef(true);
  const savedScrollTopRef = useRef(0);
  const userPinnedRef = useRef(false);

  const { language, codeText } = extractCodeParts(children);

  useLayoutEffect(() => {
    const port = scrollRef.current;
    if (port === null) {
      return;
    }

    if (userPinnedRef.current) {
      port.scrollTop = savedScrollTopRef.current;
      return;
    }

    stickToBottomRef.current = true;
    port.scrollTop = port.scrollHeight;
  }, [codeText]);

  useEffect(() => {
    const port = scrollRef.current;
    if (port === null) {
      return;
    }

    const onWheel = (event: WheelEvent) => {
      const atTop = port.scrollTop <= 0;
      const atBottom = port.scrollHeight - port.scrollTop - port.clientHeight <= 1;
      const scrollingUp = event.deltaY < 0;
      const scrollingDown = event.deltaY > 0;
      const overflow = port.scrollHeight > port.clientHeight + 1;
      const canScrollInside = overflow && ((scrollingUp && !atTop) || (scrollingDown && !atBottom));

      // Nested code scroll only — never toggle session "回到最新".
      event.stopPropagation();
      if (!canScrollInside) {
        return;
      }

      event.preventDefault();
      if (scrollingUp) {
        userPinnedRef.current = true;
        stickToBottomRef.current = false;
      }
      port.scrollTop += event.deltaY;
      savedScrollTopRef.current = port.scrollTop;
      const distanceFromBottom = port.scrollHeight - port.scrollTop - port.clientHeight;
      if (distanceFromBottom < 40) {
        userPinnedRef.current = false;
        stickToBottomRef.current = true;
      }
    };

    port.addEventListener("wheel", onWheel, { passive: false });
    return () => port.removeEventListener("wheel", onWheel);
  }, []);

  function handleScroll(event: UIEvent<HTMLDivElement>) {
    const port = event.currentTarget;
    savedScrollTopRef.current = port.scrollTop;
    const distanceFromBottom = port.scrollHeight - port.scrollTop - port.clientHeight;
    stickToBottomRef.current = distanceFromBottom < 40;
    if (stickToBottomRef.current) {
      userPinnedRef.current = false;
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
      <div
        ref={scrollRef}
        className="agentos-code-block-scroll"
        onScroll={handleScroll}
        onTouchStart={() => {
          userPinnedRef.current = true;
          stickToBottomRef.current = false;
        }}
      >
        <pre>{children}</pre>
      </div>
    </div>
  );
}

// A streaming reply calls setState on every token; react-markdown re-parses
// the whole (ever-growing) string each time it re-renders. memo() skips that
// re-parse for every OTHER message in the list whose content string hasn't
// changed since the last render — only the actively-streaming bubble re-runs.
export const AssistantMarkdown = memo(function AssistantMarkdown({ content }: { content: string }) {
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
          pre: ({ children }) => {
            const { language, codeText } = extractCodeParts(children);
            if (language === "mermaid") {
              return <MermaidBlock codeText={codeText} />;
            }
            return <CodeBlock>{children}</CodeBlock>;
          },
          // Wide GFM tables scroll horizontally instead of crushing the bubble.
          table: ({ children }) => (
            <div className="agentos-md-table-wrap">
              <table>{children}</table>
            </div>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
});
