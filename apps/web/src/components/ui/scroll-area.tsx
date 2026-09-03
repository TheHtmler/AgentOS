import * as React from "react";

import { cn } from "@/lib/utils";

function ScrollArea({ className, children, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="scroll-area"
      className={cn("relative overflow-hidden", className)}
      {...props}
    >
      <div className="h-full w-full overflow-y-auto [scrollbar-width:thin]">{children}</div>
    </div>
  );
}

function ScrollBar({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="scroll-bar" className={cn("hidden", className)} {...props} />;
}

export { ScrollArea, ScrollBar };
