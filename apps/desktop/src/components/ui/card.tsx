import type { HTMLAttributes } from "react";
import { cn } from "@/lib/cn";

export function Card({ children, className, ...props }: HTMLAttributes<HTMLElement>) {
  return (
    <section className={cn("rounded-lg border border-border bg-panel p-4 shadow-soft", className)} {...props}>
      {children}
    </section>
  );
}
