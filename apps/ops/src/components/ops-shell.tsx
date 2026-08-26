"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";
import { BookOpen, Bot, Cpu, LayoutGrid, MessageSquare, Plug, Wrench } from "lucide-react";

type NavIconName = "grid" | "book" | "bot" | "chat" | "tool" | "plug" | "chip";

type NavItem = {
  href: string;
  label: string;
  icon: NavIconName;
};

const NAV_GROUPS: { label: string; items: readonly NavItem[] }[] = [
  {
    label: "总览",
    items: [{ href: "/overview", label: "概览", icon: "grid" }],
  },
  {
    label: "配置",
    items: [
      { href: "/knowledge", label: "知识库", icon: "book" },
      { href: "/agents", label: "智能体", icon: "bot" },
      { href: "/providers", label: "模型", icon: "chip" },
      { href: "/tools", label: "工具", icon: "tool" },
    ],
  },
  {
    label: "观测",
    items: [
      { href: "/sessions", label: "会话", icon: "chat" },
      { href: "/memory", label: "记忆", icon: "book" },
    ],
  },
  {
    label: "扩展",
    items: [{ href: "/mcp", label: "MCP 接入", icon: "plug" }],
  },
];

const FLAT_NAV = NAV_GROUPS.flatMap((group) => group.items);

function NavIcon({ name }: { name: NavIconName }) {
  const cls = "size-4";
  if (name === "grid") {
    return <LayoutGrid className={cls} aria-hidden="true" />;
  }
  if (name === "book") {
    return <BookOpen className={cls} aria-hidden="true" />;
  }
  if (name === "bot") {
    return <Bot className={cls} aria-hidden="true" />;
  }
  if (name === "chat") {
    return <MessageSquare className={cls} aria-hidden="true" />;
  }
  if (name === "tool") {
    return <Wrench className={cls} aria-hidden="true" />;
  }
  if (name === "chip") {
    return <Cpu className={cls} aria-hidden="true" />;
  }
  return <Plug className={cls} aria-hidden="true" />;
}

function BrandMark({ subtitle }: { subtitle?: string }) {
  return (
    <div className="brand-mark">
      <div>
        <div className="brand">AgentOS Ops</div>
        {subtitle ? <div className="brand-sub">{subtitle}</div> : null}
      </div>
    </div>
  );
}

export function OpsShell({ subject, children }: { subject: string; children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(false);

  async function logout() {
    await fetch("/api/ops/logout", { method: "POST" });
    router.replace("/login");
    router.refresh();
  }

  function isActive(href: string) {
    if (href === "/overview") return pathname === "/" || pathname === "/overview";
    return pathname === href || pathname.startsWith(`${href}/`);
  }

  return (
    <div className="ops-frame">
      <header className="ops-top">
        <button
          type="button"
          className="ghost ops-menu-btn"
          aria-label="打开导航"
          onClick={() => setOpen(true)}
        >
          菜单
        </button>
        <BrandMark />
        <div className="ops-top-right">
          <span className="ops-subject">{subject}</span>
          <button type="button" className="ghost" onClick={() => void logout()}>
            退出
          </button>
        </div>
      </header>

      <nav className="ops-tabs" aria-label="主导航">
        {FLAT_NAV.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className={`ops-tab ${isActive(item.href) ? "is-active" : ""}`}
          >
            {item.label}
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
          <BrandMark subtitle="运营控制台" />
          <button type="button" className="ghost" onClick={() => setOpen(false)}>
            关闭
          </button>
        </div>
        <nav className="ops-nav-list">
          {NAV_GROUPS.map((group) => (
            <div key={group.label} className="ops-nav-group">
              <div className="ops-nav-label">{group.label}</div>
              {group.items.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`ops-nav-link ${isActive(item.href) ? "is-active" : ""}`}
                  onClick={() => setOpen(false)}
                >
                  <NavIcon name={item.icon} />
                  {item.label}
                </Link>
              ))}
            </div>
          ))}
        </nav>
        <div className="ops-nav-foot">审核知识 · 发布版本 · 审计会话</div>
      </aside>

      <main className="ops-main">{children}</main>
    </div>
  );
}
