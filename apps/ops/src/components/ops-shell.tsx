"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";

const NAV = [
  { href: "/", label: "概览", soon: false },
  { href: "/knowledge", label: "知识库", soon: false },
  { href: "/agents", label: "智能体", soon: false },
  { href: "/mcp", label: "MCP", soon: true },
  { href: "/skills", label: "技能", soon: true },
  { href: "/sessions", label: "会话", soon: true },
] as const;

export function OpsShell({
  subject,
  children,
}: {
  subject: string;
  children: ReactNode;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(false);

  async function logout() {
    await fetch("/api/ops/logout", { method: "POST" });
    router.replace("/login");
    router.refresh();
  }

  function isActive(href: string) {
    if (href === "/") return pathname === "/";
    return pathname === href || pathname.startsWith(`${href}/`);
  }

  return (
    <div className="ops-frame">
      <header className="ops-top">
        <button
          type="button"
          className="secondary ops-menu-btn"
          aria-label="打开导航"
          onClick={() => setOpen(true)}
        >
          菜单
        </button>
        <div className="brand">AgentOS Ops</div>
        <div className="ops-top-right">
          <span className="muted ops-subject">{subject}</span>
          <button type="button" className="secondary" onClick={() => void logout()}>
            退出
          </button>
        </div>
      </header>

      <nav className="ops-tabs" aria-label="主导航">
        {NAV.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className={`ops-tab ${isActive(item.href) ? "is-active" : ""} ${item.soon ? "is-soon" : ""}`}
          >
            {item.label}
            {item.soon ? <em>后续</em> : null}
          </Link>
        ))}
      </nav>

      {open ? (
        <button
          type="button"
          className="ops-backdrop"
          aria-label="关闭导航"
          onClick={() => setOpen(false)}
        />
      ) : null}

      <aside className={`ops-nav ${open ? "is-open" : ""}`}>
        <div className="ops-nav-head">
          <div className="brand">导航</div>
          <button type="button" className="secondary" onClick={() => setOpen(false)}>
            关闭
          </button>
        </div>
        <nav className="ops-nav-list">
          {NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={`ops-nav-link ${isActive(item.href) ? "is-active" : ""} ${item.soon ? "is-soon" : ""}`}
              onClick={() => setOpen(false)}
            >
              {item.label}
              {item.soon ? <em>后续</em> : null}
            </Link>
          ))}
        </nav>
      </aside>

      <main className="ops-main">{children}</main>
    </div>
  );
}
