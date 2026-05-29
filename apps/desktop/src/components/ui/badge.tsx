import { cn } from "@/lib/cn";

export function Badge({ label, tone = "default" }: { label: string; tone?: "default" | "success" | "warning" | "danger" }) {
  const toneClasses: Record<string, string> = {
    default: "bg-[var(--tone-neutral-bg)] text-[var(--tone-neutral-text)] border-[var(--tone-neutral-border)] backdrop-blur-sm",
    success: "bg-[var(--tone-success-bg)] text-[var(--tone-success-text)] border-[var(--tone-success-border)] backdrop-blur-sm",
    warning: "bg-[var(--tone-warning-bg)] text-[var(--tone-warning-text)] border-[var(--tone-warning-border)] backdrop-blur-sm",
    danger: "bg-[var(--tone-danger-bg)] text-[var(--tone-danger-text)] border-[var(--tone-danger-border)] backdrop-blur-sm"
  };
  return <span className={cn("inline-flex h-6 items-center rounded-full border px-2.5 text-xs font-medium", toneClasses[tone])}>{label}</span>;
}
