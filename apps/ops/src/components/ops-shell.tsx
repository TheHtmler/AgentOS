"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";

type NavIconName = "grid" | "book" | "bot" | "chat" | "spark" | "plug" | "chip";

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
    ],
  },
  {
    label: "观测",
    items: [
      { href: "/sessions", label: "会话", icon: "chat" },
      { href: "/skills", label: "技能", icon: "spark" },
      { href: "/mcp", label: "MCP", icon: "plug" },
    ],
  },
];

const FLAT_NAV = NAV_GROUPS.flatMap((group) => group.items);

function NavIcon({ name }: { name: NavIconName }) {
  const common = {
    width: 16,
    height: 16,
    viewBox: "0 0 16 16",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.6,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
  };
  if (name === "grid") {
    return (
      <svg {...common} aria-hidden>
        <rect x="2" y="2" width="5" height="5" rx="1" />
        <rect x="9" y="2" width="5" height="5" rx="1" />
        <rect x="2" y="9" width="5" height="5" rx="1" />
        <rect x="9" y="9" width="5" height="5" rx="1" />
      </svg>
    );
  }
  if (name === "book") {
    return (
      <svg {...common} aria-hidden>
        <path d="M3 3.5h5.5A2.5 2.5 0 0 1 11 6v7H5.5A2.5 2.5 0 0 0 3 10.5V3.5Z" />
        <path d="M11 6h2v7.5H6" />
      </svg>
    );
  }
  if (name === "bot") {
    return (
      <svg {...common} aria-hidden>
        <rect x="3" y="5" width="10" height="8" rx="2" />
        <path d="M8 5V3" />
        <circle cx="6.2" cy="9" r="0.7" fill="currentColor" stroke="none" />
        <circle cx="9.8" cy="9" r="0.7" fill="currentColor" stroke="none" />
      </svg>
    );
  }
  if (name === "chat") {
    return (
      <svg {...common} aria-hidden>
        <path d="M3 4.5h10v7H6.5L3 13.5V4.5Z" />
      </svg>
    );
  }
  if (name === "spark") {
    return (
      <svg {...common} aria-hidden>
        <path d="M8 2.5 9 6l3.5 1L9 8l-1 3.5L7 8 3.5 7 7 6l1-3.5Z" />
      </svg>
    );
  }
  if (name === "chip") {
    return (
      <svg {...common} aria-hidden>
        <rect x="4" y="4" width="8" height="8" rx="1.5" />
        <path d="M6 1.5v1.5M10 1.5v1.5M6 13v1.5M10 13v1.5M1.5 6H3M1.5 10H3M13 6h1.5M13 10h1.5" />
      </svg>
    );
  }
  return (
    <svg {...common} aria-hidden>
      <circle cx="8" cy="8" r="2.2" />
      <path d="M8 3v1.4M8 11.6V13M3 8h1.4M11.6 8H13M4.4 4.4l1 1M10.6 10.6l1 1M11.6 4.4l-1 1M5.4 10.6l-1 1" />
    </svg>
  );
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
