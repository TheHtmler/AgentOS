"use client";

import { useMemo, useState } from "react";
import type { ToolCallMessagePartComponent } from "@assistant-ui/react";

import {
  SandboxFilePreviewPane,
  sandboxFilesFromValue,
  summarizeToolResultContent,
  ToolCallCard,
  UploadPreviewPane,
  type SandboxFile,
  type ToolCallStatus,
} from "@/components/chat/tool-call-card";

function resultText(value: unknown): string {
  return typeof value === "string" ? value : value === undefined ? "" : JSON.stringify(value);
}

/**
 * AgentOS owns artifact and sandbox protocols. This remains a narrow slot in
 * assistant-ui's message renderer rather than duplicating its message list.
 */
export const AgentOsToolFallback: ToolCallMessagePartComponent = ({
  toolCallId,
  toolName,
  argsText,
  result,
  status,
}) => {
  const [expanded, setExpanded] = useState(status?.type === "running");
  const [sandboxFile, setSandboxFile] = useState<SandboxFile | null>(null);
  const [uploadArtifactId, setUploadArtifactId] = useState<string | null>(null);
  const resultSummary = useMemo(() => summarizeToolResultContent(resultText(result)), [result]);
  const running = status?.type === "running";
  const toolStatus: ToolCallStatus = running
    ? "running"
    : resultSummary.status === "error"
      ? "error"
      : "done";
  const resultData = resultSummary.resultData;

  return (
    <div className="grid gap-2 py-1">
      <ToolCallCard
        toolCall={{
          id: toolCallId,
          toolName,
          argsText,
          status: toolStatus,
          resultSummary: resultSummary.summary,
          resultData,
          files: sandboxFilesFromValue(resultData?.files),
          expanded,
          afterMessageId: "",
        }}
        onToggle={() => setExpanded((current) => !current)}
        selectedFilePath={sandboxFile?.path}
        onFileSelect={(file) => {
          setUploadArtifactId(null);
          setSandboxFile(file);
        }}
      />
      {toolName === "read_artifact" && typeof resultData?.artifact_id === "string" ? (
        <button
          type="button"
          className="text-left text-xs text-primary hover:underline"
          onClick={() => {
            setSandboxFile(null);
            setUploadArtifactId(resultData.artifact_id as string);
          }}
        >
          预览附件
        </button>
      ) : null}
      {sandboxFile !== null ? (
        <SandboxFilePreviewPane file={sandboxFile} onClose={() => setSandboxFile(null)} />
      ) : null}
      {uploadArtifactId !== null ? (
        <UploadPreviewPane
          artifactId={uploadArtifactId}
          onClose={() => setUploadArtifactId(null)}
        />
      ) : null}
    </div>
  );
};
