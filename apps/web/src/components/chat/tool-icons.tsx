"use client";

import type { ReactNode } from "react";

type IconProps = {
  className?: string;
};

function SvgShell({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <svg
      className={className}
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

function SearchIcon({ className }: IconProps) {
  return (
    <SvgShell className={className}>
      <circle cx="7" cy="7" r="4.25" stroke="currentColor" strokeWidth="1.25" />
      <path
        d="M10.2 10.2L13.5 13.5"
        stroke="currentColor"
        strokeWidth="1.25"
        strokeLinecap="round"
      />
    </SvgShell>
  );
}

function LinkIcon({ className }: IconProps) {
  return (
    <SvgShell className={className}>
      <path
        d="M6.5 9.5l3-3M7 11.5l-1.2 1.2a2.4 2.4 0 01-3.4-3.4L3.6 8M9 4.5l1.2-1.2a2.4 2.4 0 013.4 3.4L12.4 8"
        stroke="currentColor"
        strokeWidth="1.25"
        strokeLinecap="round"
      />
    </SvgShell>
  );
}

function DocumentIcon({ className }: IconProps) {
  return (
    <SvgShell className={className}>
      <path
        d="M4.5 2.5h5l2 2v9h-7v-11z"
        stroke="currentColor"
        strokeWidth="1.25"
        strokeLinejoin="round"
      />
      <path d="M9.5 2.5v2h2" stroke="currentColor" strokeWidth="1.25" strokeLinejoin="round" />
      <path d="M6 7.5h4M6 10h3" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" />
    </SvgShell>
  );
}

function CalcIcon({ className }: IconProps) {
  return (
    <SvgShell className={className}>
      <rect
        x="3"
        y="2.5"
        width="10"
        height="11"
        rx="1.5"
        stroke="currentColor"
        strokeWidth="1.25"
      />
      <path
        d="M5.5 5.5h5M5.5 8.5h2M8.5 8.5h2M5.5 11h2M8.5 11h2"
        stroke="currentColor"
        strokeWidth="1.25"
        strokeLinecap="round"
      />
    </SvgShell>
  );
}

function ClockIcon({ className }: IconProps) {
  return (
    <SvgShell className={className}>
      <circle cx="8" cy="8" r="5.25" stroke="currentColor" strokeWidth="1.25" />
      <path d="M8 5.5V8l2 1.5" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" />
    </SvgShell>
  );
}

function ChartIcon({ className }: IconProps) {
  return (
    <SvgShell className={className}>
      <path d="M3 12.5h10" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" />
      <path
        d="M5 10V7.5M8 10V5M11 10V8"
        stroke="currentColor"
        strokeWidth="1.25"
        strokeLinecap="round"
      />
    </SvgShell>
  );
}

function BookIcon({ className }: IconProps) {
  return (
    <SvgShell className={className}>
      <path
        d="M3.5 3.5h4.2a2 2 0 012 2v7a1.5 1.5 0 00-1.5-1.5H3.5v-7.5zM12.5 3.5H8.3a2 2 0 00-2 2v7a1.5 1.5 0 011.5-1.5h4.7v-7.5z"
        stroke="currentColor"
        strokeWidth="1.25"
        strokeLinejoin="round"
      />
    </SvgShell>
  );
}

function FolderIcon({ className }: IconProps) {
  return (
    <SvgShell className={className}>
      <path
        d="M2.5 5.5V12a1 1 0 001 1h9a1 1 0 001-1V6.5a1 1 0 00-1-1H8L6.8 4H3.5a1 1 0 00-1 1.5z"
        stroke="currentColor"
        strokeWidth="1.25"
        strokeLinejoin="round"
      />
    </SvgShell>
  );
}

function ExternalIcon({ className }: IconProps) {
  return (
    <SvgShell className={className}>
      <path
        d="M6.5 3.5H3.5v9h9v-3M8.5 3.5H12.5V7.5M12.5 3.5L7 9"
        stroke="currentColor"
        strokeWidth="1.25"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </SvgShell>
  );
}

function GearIcon({ className }: IconProps) {
  return (
    <SvgShell className={className}>
      <circle cx="8" cy="8" r="2" stroke="currentColor" strokeWidth="1.25" />
      <path
        d="M8 2.75v1.5M8 11.75v1.5M2.75 8h1.5M11.75 8h1.5M4.1 4.1l1.06 1.06M10.84 10.84l1.06 1.06M4.1 11.9l1.06-1.06M10.84 5.16l1.06-1.06"
        stroke="currentColor"
        strokeWidth="1.25"
        strokeLinecap="round"
      />
    </SvgShell>
  );
}

export function ToolIcon({ toolName, className }: { toolName: string; className?: string }) {
  if (toolName === "web_search") {
    return <SearchIcon className={className} />;
  }
  if (toolName === "fetch_url") {
    return <LinkIcon className={className} />;
  }
  if (toolName === "read_artifact") {
    return <DocumentIcon className={className} />;
  }
  if (toolName === "calculate") {
    return <CalcIcon className={className} />;
  }
  if (toolName === "time_diff") {
    return <ClockIcon className={className} />;
  }
  if (toolName === "growth_assess") {
    return <ChartIcon className={className} />;
  }
  if (toolName === "knowledge_search") {
    return <BookIcon className={className} />;
  }
  if (toolName.startsWith("case_")) {
    return <FolderIcon className={className} />;
  }
  if (toolName.startsWith("mcp_")) {
    return <ExternalIcon className={className} />;
  }
  return <GearIcon className={className} />;
}
