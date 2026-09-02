"use client";

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  displayCaseFactContent,
  parseCaseFacts,
  parseCaseSummaries,
  type CaseFact,
} from "@/lib/cases";

type PendingCaseFactsBannerProps = {
  agentId: string | null;
  caseEnabled: boolean;
  /** Bump to force a refetch, e.g. when a run finishes. */
  refreshKey: number;
};

async function loadDefaultCaseId(agentId: string, signal: AbortSignal): Promise<string | null> {
  const response = await fetch(`/api/cases?agent_id=${encodeURIComponent(agentId)}`, {
    cache: "no-store",
    signal,
  });
  if (!response.ok) {
    return null;
  }
  const cases = parseCaseSummaries((await response.json()) as unknown);
  if (cases === null || cases.length === 0) {
    return null;
  }
  return (cases.find((item) => item.is_default) ?? cases[0]).id;
}

async function loadProposedFacts(caseId: string, signal: AbortSignal): Promise<CaseFact[]> {
  const response = await fetch(`/api/cases/${caseId}/facts`, { cache: "no-store", signal });
  if (!response.ok) {
    return [];
  }
  const facts = parseCaseFacts((await response.json()) as unknown);
  return (facts ?? []).filter((fact) => fact.status === "proposed");
}

export function PendingCaseFactsBanner({
  agentId,
  caseEnabled,
  refreshKey,
}: PendingCaseFactsBannerProps) {
  const [caseId, setCaseId] = useState<string | null>(null);
  const [facts, setFacts] = useState<CaseFact[]>([]);
  const [expanded, setExpanded] = useState(false);
  const [actioningId, setActioningId] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let isCurrent = true;

    const load = async () => {
      if (!caseEnabled || agentId === null) {
        setCaseId(null);
        setFacts([]);
        return;
      }
      const resolvedCaseId = await loadDefaultCaseId(agentId, controller.signal);
      if (!isCurrent) {
        return;
      }
      setCaseId(resolvedCaseId);
      const proposed =
        resolvedCaseId === null ? [] : await loadProposedFacts(resolvedCaseId, controller.signal);
      if (isCurrent) {
        setFacts(proposed);
      }
    };

    void load().catch(() => undefined);
    // Background extraction after a run is fire-and-forget and can lag the
    // "run finalized" signal by a couple seconds; one delayed re-check
    // catches that without resorting to interval polling.
    const delayed = window.setTimeout(() => void load().catch(() => undefined), 4_000);

    return () => {
      isCurrent = false;
      controller.abort();
      window.clearTimeout(delayed);
    };
  }, [agentId, caseEnabled, refreshKey]);

  const handleFactAction = useCallback(
    async (factId: string, action: "confirm" | "reject") => {
      if (caseId === null) {
        return;
      }
      setActioningId(factId);
      try {
        const response = await fetch(`/api/cases/${caseId}/facts/${factId}/${action}`, {
          method: "POST",
        });
        if (response.ok) {
          setFacts((current) => current.filter((fact) => fact.id !== factId));
        }
      } finally {
        setActioningId(null);
      }
    },
    [caseId],
  );

  if (!caseEnabled || facts.length === 0) {
    return null;
  }

  return (
    <div className="agentos-pending-facts-banner">
      <button
        type="button"
        className="agentos-pending-facts-summary"
        onClick={() => setExpanded((current) => !current)}
        aria-expanded={expanded}
      >
        <span>{facts.length} 项待确认的档案更新</span>
        <span className="agentos-pending-facts-toggle">{expanded ? "收起" : "查看"}</span>
      </button>

      {expanded ? (
        <ul className="agentos-pending-facts-list">
          {facts.map((fact) => (
            <li key={fact.id} className="agentos-pending-facts-item">
              <div className="agentos-pending-facts-detail">
                <span className="agentos-pending-facts-content">
                  {displayCaseFactContent(fact)}
                </span>
                {fact.tags.length > 0 ? (
                  <span className="agentos-pending-facts-tags">{fact.tags.join(" · ")}</span>
                ) : null}
              </div>
              <div className="agentos-pending-facts-actions">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="min-w-14"
                  disabled={actioningId === fact.id}
                  onClick={() => void handleFactAction(fact.id, "reject")}
                >
                  {actioningId === fact.id ? "处理中…" : "否定"}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  className="min-w-14"
                  disabled={actioningId === fact.id}
                  onClick={() => void handleFactAction(fact.id, "confirm")}
                >
                  {actioningId === fact.id ? "处理中…" : "确认"}
                </Button>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
