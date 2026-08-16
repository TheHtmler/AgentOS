import type { ReactNode } from "react";

export function PageHeader({
  title,
  lead,
  actions,
}: {
  title: string;
  lead?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-head">
      <div className="page-head__copy">
        <h1 className="page-title">{title}</h1>
        {lead ? <p className="muted page-lead">{lead}</p> : null}
      </div>
      {actions ? <div className="page-head__actions">{actions}</div> : null}
    </header>
  );
}
