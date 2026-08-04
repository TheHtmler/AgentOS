"use client";

import { useSyncExternalStore } from "react";

import { useTheme } from "@/components/theme/theme-provider";

type ThemeToggleProps = {
  className?: string;
  compact?: boolean;
};

const subscribeToHydration = () => () => {};
const getClientHydrationSnapshot = () => true;
const getServerHydrationSnapshot = () => false;

export function ThemeToggle({ className = "", compact = false }: ThemeToggleProps) {
  const { theme, toggleTheme } = useTheme();
  const mounted = useSyncExternalStore(
    subscribeToHydration,
    getClientHydrationSnapshot,
    getServerHydrationSnapshot,
  );

  const isDark = mounted ? theme === "dark" : false;

  return (
    <button
      type="button"
      onClick={toggleTheme}
      aria-label={isDark ? "切换到浅色主题" : "切换到深色主题"}
      aria-pressed={isDark}
      title={isDark ? "浅色" : "深色"}
      className={`agentos-theme-toggle ${compact ? "agentos-theme-toggle-compact" : ""} ${className}`.trim()}
    >
      <span className="agentos-theme-toggle-icon" aria-hidden="true">
        {isDark ? (
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
            <circle cx="12" cy="12" r="4.2" />
            <path
              strokeLinecap="round"
              d="M12 3.2v1.8M12 19v1.8M4.9 4.9l1.3 1.3M17.8 17.8l1.3 1.3M3.2 12h1.8M19 12h1.8M4.9 19.1l1.3-1.3M17.8 6.2l1.3-1.3"
            />
          </svg>
        ) : (
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M16.4 14.6A6.4 6.4 0 0 1 9.4 7.6c0-.4 0-.8.1-1.2A7.2 7.2 0 1 0 17.6 14.5c-.4.1-.8.1-1.2.1Z"
            />
          </svg>
        )}
      </span>
      {compact ? null : <span suppressHydrationWarning>{isDark ? "浅色" : "深色"}</span>}
    </button>
  );
}
