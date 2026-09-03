import * as React from "react";

import { cn } from "@/lib/utils";

function TooltipProvider({
  delayDuration = 300,
  ...props
}: React.ComponentProps<"div"> & { delayDuration?: number }) {
  return <div data-slot="tooltip-provider" data-delay={delayDuration} {...props} />;
}

function Tooltip({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="tooltip" className={cn("relative", className)} {...props} />;
}

function TooltipTrigger({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="tooltip-trigger" className={cn("inline-flex", className)} {...props} />;
}

function TooltipContent({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="tooltip-content"
      className={cn(
        "bg-primary text-primary-foreground animate-in fade-in-0 zoom-in-95 z-50 max-w-xs rounded-md px-3 py-1.5 text-xs",
        className,
      )}
      {...props}
    />
  );
}

export { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider };
