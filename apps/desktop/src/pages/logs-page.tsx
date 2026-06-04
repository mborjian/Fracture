import { useEffect, useMemo, useState } from "react";
import { Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";
import { useUiSettingsQuery } from "@/hooks/useBackendQuery";
import { useAppStore } from "@/store/useAppStore";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";

const LOG_FILTERS = [
  { id: "all", label: "All" },
  { id: "info", label: "Info" },
  { id: "debug", label: "Debug" },
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
  const { data: uiSettings } = useUiSettingsQuery();
  const [filter, setFilter] = useState<(typeof LOG_FILTERS)[number]["id"]>("all");
  const showDevelopmentLogs = uiSettings?.showDevelopmentLogs ?? false;
  const visibleFilters = useMemo(
    () => LOG_FILTERS.filter((item) => showDevelopmentLogs || item.id !== "debug"),
    [showDevelopmentLogs]
  );

  useEffect(() => {
    if (!showDevelopmentLogs && filter === "debug") {
      setFilter("all");
    }
  }, [filter, showDevelopmentLogs]);

  const sortedLogs = useMemo(
    () =>
      [...logs].sort((left, right) => {
        const timeDiff = Date.parse(right.ts) - Date.parse(left.ts);
        if (Number.isFinite(timeDiff) && timeDiff !== 0) return timeDiff;
        return right.id.localeCompare(left.id);
      }),
    [logs]
  );

  const filtered = useMemo(
    () =>
      sortedLogs.filter((line) => {
        if (!showDevelopmentLogs && line.level === "debug") return false;
        if (filter === "all") return true;
        return line.level === filter;
      }),
    [filter, showDevelopmentLogs, sortedLogs]
  );

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <ToggleGroup type="single" value={filter} className="mx-auto w-[352px]"
          onValueChange={(value) => {
            if (value) setFilter(value as LogFilter);
          }}
        >
          {visibleFilters.map((item) => (
            <ToggleGroupItem key={item.id} value={item.id}>
              {item.label}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>

        <Button variant="secondary" size="sm" className="h-8 w-8 rounded-full p-0" onClick={clearLogs} title="Clear logs" aria-label="Clear logs">
          <Trash2 className="h-4 w-4" />
        </Button>
      </div>

      <div className="h-[calc(100vh-240px)] min-h-[280px] w-full overflow-y-auto rounded-xl border border-border bg-panelAlt p-2 font-mono text-xs">
        {filtered.length === 0 ? <div className="text-textMuted">No logs for this filter.</div> : null}
        <div className="space-y-1">
          {filtered.map((line) => (
            <div
              key={line.id}
              className={cn(
                "rounded px-2 py-1 hover:bg-panel",
                showDevelopmentLogs ? "space-y-1" : ""
              )}
            >
              <div className={cn("grid gap-3", showDevelopmentLogs ? "grid-cols-[88px_70px_96px_1fr]" : "grid-cols-[88px_70px_1fr]")}>
                <span className="text-textMuted">{formatShortLogTime(line.ts)}</span>
                <span className={cn("uppercase", line.level === "error" ? "text-danger" : line.level === "warning" ? "text-warning" : "text-accent")}>
                  {line.level}
                </span>
                {showDevelopmentLogs ? (
                  <span className="inline-flex h-5 max-w-full items-center justify-center rounded-full border border-border/70 bg-panel px-2 py-0.5 text-[10px] uppercase tracking-wide text-textMuted shrink-0 align-top">
                    {line.source ?? "ui"}
                  </span>
                ) : null}
                <span>{line.message}</span>
              </div>
              {showDevelopmentLogs && line.trace ? (
                <pre className="overflow-x-auto rounded-xl border border-border/70 bg-panel px-2 py-2 text-[11px] leading-4 text-textMuted whitespace-pre-wrap break-all">
                  {line.trace}
                </pre>
              ) : null}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
