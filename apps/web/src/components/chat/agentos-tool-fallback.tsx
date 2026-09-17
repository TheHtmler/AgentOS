"use client";

import { useMemo, useState } from "react";
import type { ToolCallMessagePartComponent } from "@assistant-ui/react";

import {
  ToolFallback,
  ToolFallbackArgs,
  ToolFallbackContent,
  ToolFallbackError,
  ToolFallbackResult,
  ToolFallbackRoot,
  ToolFallbackTrigger,
} from "@/components/assistant-ui/elements/tool-fallback.aui";
import { ApprovalPanel } from "@/components/chat/approval-panel";
import {
  SandboxFilePreviewPane,
  sandboxFilesFromValue,
  summarizeToolResultContent,
  UploadPreviewPane,
  type SandboxFile,
} from "@/components/chat/tool-call-card";
import { toolDisplayName } from "@/components/chat/tool-labels";

function resultText(value: unknown): string {
  return typeof value === "string" ? value : value === undefined ? "" : JSON.stringify(value);
}

/**
 * AgentOS owns artifact and sandbox protocols. The generic tool lifecycle is
 * still rendered by assistant-ui; only those domain-specific previews extend it.
 */
export const AgentOsToolFallback: ToolCallMessagePartComponent = (props) => {
  const { toolCallId, toolName, args, argsText, result, status } = props;
  const [expanded, setExpanded] = useState(status?.type === "running");
  const [sandboxFile, setSandboxFile] = useState<SandboxFile | null>(null);
  const [uploadArtifactId, setUploadArtifactId] = useState<string | null>(null);
  const resultSummary = useMemo(() => summarizeToolResultContent(resultText(result)), [result]);
  const resultData = resultSummary.resultData;

  return (
    <ToolFallbackRoot data-tool-call-id={toolCallId} open={expanded} onOpenChange={setExpanded}>
      <ToolFallbackTrigger toolName={toolDisplayName(toolName)} status={status} />
      <ToolFallbackContent>
        <ToolFallbackError status={status} />
        <ToolFallbackArgs argsText={argsText} />
        {toolName === "case_slot_collect" && status?.type === "requires-action" ? (
          <ApprovalPanel
            toolCallId={toolCallId}
            toolArgs={args && typeof args === "object" ? args : {}}
            respondToApproval={props.respondToApproval}
          />
        ) : (
          <ToolFallback.Approval
            status={status}
            approval={props.approval}
            interrupt={props.interrupt}
            addResult={props.addResult}
            resume={props.resume}
            respondToApproval={props.respondToApproval}
          />
        )}
        <ToolFallbackResult result={result} />
        {toolName === "read_artifact" && typeof resultData?.artifact_id === "string" ? (
          <button
            type="button"
            className="w-fit text-xs text-primary hover:underline"
            onClick={() => {
              setSandboxFile(null);
              setUploadArtifactId(resultData.artifact_id as string);
            }}
          >
            预览附件
          </button>
        ) : null}
        {toolName === "sandbox_exec"
          ? sandboxFilesFromValue(resultData?.files).map((file) => (
              <button
                key={file.path}
                type="button"
                className="w-fit text-left text-xs text-primary hover:underline"
                onClick={() => {
                  setUploadArtifactId(null);
                  setSandboxFile(file);
                }}
              >
                预览 {file.path}
              </button>
            ))
          : null}
        {sandboxFile !== null ? (
          <SandboxFilePreviewPane file={sandboxFile} onClose={() => setSandboxFile(null)} />
        ) : null}
        {uploadArtifactId !== null ? (
          <UploadPreviewPane
            artifactId={uploadArtifactId}
            onClose={() => setUploadArtifactId(null)}
          />
        ) : null}
      </ToolFallbackContent>
    </ToolFallbackRoot>
  );
};
