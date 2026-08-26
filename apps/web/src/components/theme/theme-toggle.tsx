"use client";

import { useSyncExternalStore } from "react";
import { Moon, Sun } from "lucide-react";

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
        {isDark ? <Sun className="size-4" /> : <Moon className="size-4" />}
      </span>
      {compact ? null : <span suppressHydrationWarning>{isDark ? "浅色" : "深色"}</span>}
    </button>
  );
}
