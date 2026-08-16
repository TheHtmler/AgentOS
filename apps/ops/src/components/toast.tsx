"use client";

import { useCallback, useEffect, useState } from "react";

export function useToast() {
  const [text, setText] = useState<string | null>(null);

  const show = useCallback((message: string) => {
    setText(message);
  }, []);

  useEffect(() => {
    if (!text) return;
    const timer = window.setTimeout(() => setText(null), 2400);
    return () => window.clearTimeout(timer);
  }, [text]);

  return {
    show,
    node: text ? (
      <div className="toast" role="status" aria-live="polite">
        {text}
      </div>
    ) : null,
  };
}
