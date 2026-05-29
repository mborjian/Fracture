import { cn } from "@/lib/cn";

export function Badge({ label, tone = "default" }: { label: string; tone?: "default" | "success" | "warning" | "danger" }) {
  const toneClasses: Record<string, string> = {
    default: "bg-panelAlt/75 text-textMuted border-border backdrop-blur-sm",
    success: "bg-[#0f2a23]/65 text-success border-[#1f513f] backdrop-blur-sm",
    warning: "bg-[#2a210f]/65 text-warning border-[#5b4615] backdrop-blur-sm",
    danger: "bg-[#2e1616]/65 text-danger border-[#5b2020] backdrop-blur-sm"
  };
  return <span className={cn("inline-flex h-6 items-center rounded-full border px-2.5 text-xs font-medium", toneClasses[tone])}>{label}</span>;
}
