import type { DictationAdapter } from "@assistant-ui/react";

type DictationListeners = {
  speechStart: Set<() => void>;
  speechEnd: Set<(result: DictationAdapter.Result) => void>;
  speech: Set<(result: DictationAdapter.Result) => void>;
};

function notify<T>(listeners: Iterable<(value: T) => void>, value: T): void {
  for (const listener of listeners) {
    try {
      listener(value);
    } catch (error) {
      console.error("[dictation] listener failed", error);
    }
  }
}

/**
 * Bridges AgentOS's server-side transcription endpoint to assistant-ui's
 * composer lifecycle. The final transcript is inserted into the draft rather
 * than submitted, so the user can correct it before starting a run.
 */
export class AudioTranscriptionDictationAdapter implements DictationAdapter {
  disableInputDuringDictation = true;

  listen(): DictationAdapter.Session {
    const listeners: DictationListeners = {
      speechStart: new Set(),
      speechEnd: new Set(),
      speech: new Set(),
    };
    let recorder: MediaRecorder | null = null;
    let stream: MediaStream | null = null;
    let cancelled = false;
    let stopped = false;
    let resolveStopped: (() => void) | null = null;
    const stoppedPromise = new Promise<void>((resolve) => {
      resolveStopped = resolve;
    });

    const session: DictationAdapter.Session = {
      status: { type: "starting" },
      stop: async () => {
        if (stopped) return stoppedPromise;
        stopped = true;
        if (recorder?.state === "recording") {
          recorder.stop();
        } else {
          finish("stopped");
        }
        return stoppedPromise;
      },
      cancel: () => {
        cancelled = true;
        stopped = true;
        if (recorder?.state === "recording") recorder.stop();
        finish("cancelled");
      },
      onSpeechStart: (callback) => {
        listeners.speechStart.add(callback);
        return () => listeners.speechStart.delete(callback);
      },
      onSpeechEnd: (callback) => {
        listeners.speechEnd.add(callback);
        return () => listeners.speechEnd.delete(callback);
      },
      onSpeech: (callback) => {
        listeners.speech.add(callback);
        return () => listeners.speech.delete(callback);
      },
    };

    const finish = (reason: "stopped" | "cancelled" | "error") => {
      if (session.status.type === "ended") return;
      stream?.getTracks().forEach((track) => track.stop());
      stream = null;
      session.status = { type: "ended", reason };
      resolveStopped?.();
      resolveStopped = null;
    };

    void (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        if (cancelled || stopped) {
          finish(cancelled ? "cancelled" : "stopped");
          return;
        }

        const chunks: Blob[] = [];
        recorder = new MediaRecorder(stream);
        recorder.ondataavailable = (event) => {
          if (event.data.size > 0) chunks.push(event.data);
        };
        recorder.onstop = () => {
          void (async () => {
            try {
              if (!cancelled && chunks.length > 0) {
                const mimeType = recorder?.mimeType || "audio/webm";
                const formData = new FormData();
                formData.append(
                  "file",
                  new File([new Blob(chunks, { type: mimeType })], "recording.webm", {
                    type: mimeType,
                  }),
                );
                const response = await fetch("/api/audio/transcriptions", {
                  method: "POST",
                  body: formData,
                });
                const payload: unknown = await response.json().catch(() => null);
                const transcript =
                  typeof payload === "object" &&
                  payload !== null &&
                  "text" in payload &&
                  typeof payload.text === "string"
                    ? payload.text.trim()
                    : "";
                if (response.ok && transcript) {
                  const result = { transcript, isFinal: true };
                  notify(listeners.speech, result);
                  notify(listeners.speechEnd, result);
                }
              }
              finish("stopped");
            } catch {
              finish("error");
            }
          })();
        };
        recorder.start();
        session.status = { type: "running" };
        for (const callback of listeners.speechStart) callback();
      } catch {
        finish("error");
      }
    })();

    return session;
  }
}
