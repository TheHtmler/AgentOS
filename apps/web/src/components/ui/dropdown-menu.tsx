"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

function DropdownMenu({ className, ...props }: React.ComponentProps<"details">) {
  return <details data-slot="dropdown-menu" className={cn("relative", className)} {...props} />;
}

function DropdownMenuTrigger({ className, ...props }: React.ComponentProps<"summary">) {
  return (
    <summary
      data-slot="dropdown-menu-trigger"
      className={cn(
        "list-none rounded-md outline-none marker:content-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden",
        className,
      )}
      {...props}
    />
  );
}

function DropdownMenuContent({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="dropdown-menu-content"
      role="menu"
      className={cn(
        "absolute top-full right-0 z-50 mt-1 min-w-36 rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-md",
        className,
      )}
      {...props}
    />
  );
}

function DropdownMenuItem({ className, ...props }: React.ComponentProps<"button">) {
  return (
    <button
      type="button"
      role="menuitem"
      data-slot="dropdown-menu-item"
      className={cn(
        "flex min-h-9 w-full items-center rounded-sm px-2 py-1.5 text-left text-xs transition-colors outline-none hover:bg-accent focus-visible:bg-accent disabled:pointer-events-none disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}

export { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger };
