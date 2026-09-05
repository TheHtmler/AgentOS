"use client";

import { useEffect, useState } from "react";
import { useAuiState } from "@assistant-ui/react";

import { ComposerVoice } from "@/components/assistant-ui/elements/composer";

/**
 * `ComposerVoice` is a presentation element, while Dictation is a runtime
 * primitive. This bridge keeps the generated Thread's microphone controls
 * and exposes the runtime recording state in the same Composer surface.
 */
export function ComposerDictationVoice() {
  const dictation = useAuiState((state) => state.composer.dictation);
  return dictation === undefined ? null : <ActiveComposerDictationVoice />;
}

function ActiveComposerDictationVoice() {
  const [startedAt] = useState(() => Date.now());
  const [now, setNow] = useState(startedAt);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(timer);
  }, []);

  const seconds = Math.max(0, Math.floor((now - startedAt) / 1_000));
  return (
    <ComposerVoice
      recording
      seconds={seconds}
      className="absolute inset-x-0 bottom-full z-20 mb-1 rounded-lg border border-border/60 bg-card/95 px-2 shadow-sm backdrop-blur-sm"
    />
  );
}
