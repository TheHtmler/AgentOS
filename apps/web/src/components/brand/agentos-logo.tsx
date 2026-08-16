type AgentOsLogoProps = {
  className?: string;
  showWordmark?: boolean;
  subtitle?: string;
};

export function AgentOsLogo({ className = "", showWordmark = true, subtitle }: AgentOsLogoProps) {
  return (
    <div className={`flex min-w-0 items-center gap-3 ${className}`}>
      <span aria-hidden="true" className="agentos-logo-mark agentos-accent-glow">
        <svg viewBox="0 0 32 32" className="h-5 w-5" fill="none" xmlns="http://www.w3.org/2000/svg">
          <circle cx="16" cy="16" r="3.2" fill="currentColor" />
          <circle
            cx="16"
            cy="16"
            r="7.5"
            stroke="currentColor"
            strokeOpacity="0.55"
            strokeWidth="1.2"
          />
          <circle cx="16" cy="7.5" r="1.6" fill="currentColor" />
          <circle cx="24" cy="20.5" r="1.6" fill="currentColor" />
          <circle cx="8" cy="20.5" r="1.6" fill="currentColor" />
          <path
            d="M16 9.2V12.4M21.8 19.2L19.2 17.6M10.2 19.2L12.8 17.6"
            stroke="currentColor"
            strokeOpacity="0.75"
            strokeWidth="1.1"
            strokeLinecap="round"
          />
        </svg>
      </span>
      {showWordmark ? (
        <div className="min-w-0">
          <p className="truncate text-base font-semibold tracking-tight text-[var(--text)]">
            AgentOS
          </p>
          {subtitle ? (
            <p className="hidden truncate text-xs text-[var(--muted)] sm:block">{subtitle}</p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
