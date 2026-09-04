"use client";

import * as React from "react";
import { X } from "lucide-react";

import { cn } from "@/lib/utils";

type SheetContextValue = { close: () => void };
const SheetContext = React.createContext<SheetContextValue | null>(null);

function Sheet({
  open,
  onOpenChange,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  children: React.ReactNode;
}) {
  return (
    <SheetContext.Provider value={{ close: () => onOpenChange(false) }}>
      <SheetDialog open={open} onOpenChange={onOpenChange}>
        {children}
      </SheetDialog>
    </SheetContext.Provider>
  );
}

function SheetDialog({
  open,
  onOpenChange,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  children: React.ReactNode;
}) {
  const ref = React.useRef<HTMLDialogElement>(null);

  React.useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      data-slot="sheet"
      className="m-0 h-dvh max-h-none w-[min(88vw,22rem)] max-w-none border-0 bg-transparent p-0 backdrop:bg-black/45"
      onCancel={(event) => {
        event.preventDefault();
        onOpenChange(false);
      }}
      onClose={() => onOpenChange(false)}
      onClick={(event) => {
        if (event.target === event.currentTarget) onOpenChange(false);
      }}
    >
      {children}
    </dialog>
  );
}

function SheetContent({ className, ...props }: React.ComponentProps<"aside">) {
  return (
    <aside
      data-slot="sheet-content"
      className={cn("flex h-full flex-col border-r border-border bg-card shadow-xl", className)}
      {...props}
    />
  );
}

function SheetHeader({ className, ...props }: React.ComponentProps<"header">) {
  return <header className={cn("border-b border-border px-4 py-3 pr-14", className)} {...props} />;
}

function SheetTitle({ className, ...props }: React.ComponentProps<"h2">) {
  return <h2 className={cn("text-sm font-semibold text-foreground", className)} {...props} />;
}

function SheetDescription({ className, ...props }: React.ComponentProps<"p">) {
  return <p className={cn("text-xs text-muted-foreground", className)} {...props} />;
}

function SheetClose({ className, children, ...props }: React.ComponentProps<"button">) {
  const context = React.useContext(SheetContext);
  return (
    <button
      type="button"
      data-slot="sheet-close"
      className={cn(
        "inline-flex min-h-11 items-center justify-center rounded-md px-3 text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
        className,
      )}
      onClick={(event) => {
        props.onClick?.(event);
        if (!event.defaultPrevented) context?.close();
      }}
      {...props}
    >
      {children ?? (
        <>
          <X aria-hidden="true" className="size-4" />
          <span className="sr-only">关闭</span>
        </>
      )}
    </button>
  );
}

export { Sheet, SheetClose, SheetContent, SheetDescription, SheetHeader, SheetTitle };
