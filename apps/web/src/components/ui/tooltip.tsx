import * as React from "react";

import { cn } from "@/lib/utils";

function TooltipProvider({
  delayDuration = 300,
  ...props
}: React.ComponentProps<"div"> & { delayDuration?: number }) {
  return <div data-slot="tooltip-provider" data-delay={delayDuration} {...props} />;
}

function Tooltip({ className, ...props }: React.ComponentProps<"span">) {
  return (
    <span data-slot="tooltip" className={cn("group relative inline-flex", className)} {...props} />
  );
}

function TooltipTrigger({ className, ...props }: React.ComponentProps<"span">) {
  return <span data-slot="tooltip-trigger" className={cn("inline-flex", className)} {...props} />;
}

function TooltipContent({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <span
      role="tooltip"
      data-slot="tooltip-content"
      className={cn(
        "invisible absolute bottom-full left-1/2 z-50 mb-2 max-w-xs -translate-x-1/2 rounded-md bg-primary px-3 py-1.5 text-xs whitespace-nowrap text-primary-foreground opacity-0 shadow-md transition-opacity group-focus-within:visible group-focus-within:opacity-100 group-hover:visible group-hover:opacity-100",
        className,
      )}
      {...props}
    />
  );
}

export { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider };
