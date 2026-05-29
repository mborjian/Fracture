import { useMemo, useState } from "react";
import { Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";
import { useAppStore } from "@/store/useAppStore";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";

const LOG_FILTERS = [
  { id: "all", label: "All" },
  { id: "info", label: "Info" },
  { id: "warning", label: "Warn" },
  { id: "error", label: "Error" },
] as const;

type LogFilter = typeof LOG_FILTERS[number]["id"];

function formatShortLogTime(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleTimeString([], { hour12: false });
}

export function LogsPage() {
  const logs = useAppStore((s) => s.logs);
  const clearLogs = useAppStore((s) => s.clearLogs);
  const [filter, setFilter] = useState<(typeof LOG_FILTERS)[number]["id"]>("all");

  const filtered = useMemo(
    () =>
      logs.filter((line) => {
        if (filter === "all") return true;
        return line.level === filter;
      }),
    [filter, logs]
  );

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <ToggleGroup type="single" value={filter} className="mx-auto w-[280px]"
          onValueChange={(value) => {
            if (value) setFilter(value as LogFilter);
          }}
        >
          {LOG_FILTERS.map((item) => (
            <ToggleGroupItem key={item.id} value={item.id}>
              {item.label}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>

        <Button variant="secondary" size="sm" className="h-8 w-8 rounded-full p-0" onClick={clearLogs} title="Clear logs" aria-label="Clear logs">
          <Trash2 className="h-4 w-4" />
        </Button>
      </div>

      <div className="h-[calc(100vh-240px)] min-h-[280px] w-full overflow-y-auto rounded-md border border-border bg-panelAlt p-2 font-mono text-xs">
        {filtered.length === 0 ? <div className="text-textMuted">No logs for this filter.</div> : null}
        <div className="space-y-1">
          {filtered.map((line) => (
            <div key={line.id} className="grid grid-cols-[88px_70px_1fr] gap-3 rounded px-2 py-1 hover:bg-panel">
              <span className="text-textMuted">{formatShortLogTime(line.ts)}</span>
              <span className={cn("uppercase", line.level === "error" ? "text-danger" : line.level === "warning" ? "text-warning" : "text-accent")}>
                {line.level}
              </span>
              <span>{line.message}</span>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
