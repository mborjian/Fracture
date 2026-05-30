import * as React from "react";
import { cn } from "@/lib/cn";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => <input ref={ref} className={cn("h-9 w-full rounded-xl border border-border bg-panelAlt px-3 text-sm text-text placeholder:text-textMuted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent", className)} {...props} />
);

Input.displayName = "Input";
