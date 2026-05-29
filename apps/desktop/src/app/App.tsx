import { useEffect, type ComponentType } from "react";
import { CircleHelp, Cog, LayoutDashboard, ListTree, Logs } from "lucide-react";
import { motion } from "framer-motion";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { useBackendHealth } from "@/hooks/useBackendQuery";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { connectRealtime } from "@/lib/ws";
import { useAppStore } from "@/store/useAppStore";
import { DashboardPage } from "@/pages/dashboard-page";
import { AboutPage } from "@/pages/about-page";
import { ProfilesPage } from "@/pages/profiles-page";
import { SettingsPage } from "@/pages/settings-page";
import { LogsPage } from "@/pages/logs-page";
import type { ConnectionState, NavPage } from "@/types";

const TOP_NAV: Array<{ id: NavPage; label: string; icon: ComponentType<{ className?: string }> }> = [
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard },
  { id: "profiles", label: "Profiles", icon: ListTree },
  { id: "settings", label: "Settings", icon: Cog },
  { id: "logs", label: "Logs", icon: Logs },
  { id: "about", label: "About", icon: CircleHelp },
];

function ActivePage() {
  const page = useAppStore((s) => s.page);
  switch (page) {
    case "dashboard":
      return <DashboardPage />;
    case "profiles":
      return <ProfilesPage />;
    case "about":
      return <AboutPage />;
    case "settings":
      return <SettingsPage />;
    case "logs":
      return <LogsPage />;
    default:
      return <DashboardPage />;
  }
}

function stateBadgeMeta(state: ConnectionState) {
  if (state === "running") {
    return {
      label: "Connected",
      bulletClass: "bg-success shadow-[0_0_12px_rgba(34,197,94,0.85)]",
      badgeClass: "border-[#2b5f47] bg-[rgba(34,197,94,0.09)] text-[#9ee6b9]",
    };
  }
  if (state === "starting") {
    return {
      label: "Connecting",
      bulletClass: "bg-warning shadow-[0_0_12px_rgba(234,179,8,0.85)]",
      badgeClass: "border-[#6a5820] bg-[rgba(234,179,8,0.08)] text-[#f5dd9a]",
    };
  }
  return {
    label: "Disconnected",
    bulletClass: "bg-slate-400 shadow-[0_0_12px_rgba(148,163,184,0.8)]",
    badgeClass: "border-[#3d4b63] bg-[rgba(148,163,184,0.08)] text-[#c3cfde]",
  };
}

export function App() {
  const page = useAppStore((s) => s.page);
  const setPage = useAppStore((s) => s.setPage);
  const connectionState = useAppStore((s) => s.connectionState);
  const setStatus = useAppStore((s) => s.setStatus);
  const { isError } = useBackendHealth();
  const statusMeta = stateBadgeMeta(connectionState);

  useEffect(() => {
    const disconnect = connectRealtime();
    return disconnect;
  }, []);

  useEffect(() => {
    void api
      .status()
      .then((status) => setStatus(status))
      .catch(() => {
        // Realtime connection and health checks surface backend availability.
      });
  }, [setStatus]);

  useEffect(() => {
    if (isError) {
      toast.error("Backend daemon is unreachable");
    }
  }, [isError]);

  useEffect(() => {
    void api
      .uiSettings()
      .then((settings) => {
        const theme = settings.theme ?? "system";
        localStorage.setItem("fracture-ui-theme", theme);
        const resolved = theme === "system"
          ? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
          : theme;
        document.documentElement.setAttribute("data-theme", resolved);
      })
      .catch(() => {});
  }, []);

  return (
    <div className="flex h-full flex-col gap-3 p-3">
      <header className="grid h-14 grid-cols-[1fr_auto_1fr] items-center rounded-lg border border-border bg-panel px-4">
        <div className="flex items-center gap-2">
          <img src="/logo.png" alt="Fracture" className="h-7 w-7 rounded-md" />
          <span className="text-sm font-semibold">Fracture</span>
        </div>

        <div className="flex items-center gap-2">
          {TOP_NAV.map((item) => {
            const Icon = item.icon;
            const active = page === item.id;
            return (
              <button
                key={item.id}
                title={item.label}
                onClick={() => setPage(item.id)}
                className={cn(
                  "flex h-10 items-center justify-center overflow-hidden rounded-xl border text-sm leading-none transition-all duration-300",
                  active
                    ? "w-auto border-border bg-panelAlt px-3 text-text"
                    : "w-10 justify-center border-transparent bg-transparent text-textMuted hover:border-border hover:bg-panelAlt hover:text-text"
                )}
              >
                <Icon className="h-4 w-4 shrink-0" />
                <span
                  className={cn(
                    "self-center whitespace-nowrap transition-all duration-200",
                    active ? "ml-2 max-w-[180px] opacity-100" : "ml-0 max-w-0 opacity-0"
                  )}
                >
                  {item.label}
                </span>
              </button>
            );
          })}
        </div>

        <div className="justify-self-end">
          <span className={cn("inline-flex h-7 items-center justify-center gap-2 rounded-full border px-3 text-xs font-semibold backdrop-blur-sm", statusMeta.badgeClass)}>
            <span className={cn("h-2 w-2 rounded-full", statusMeta.bulletClass)} />
            {statusMeta.label}
          </span>
        </div>
      </header>

      <motion.main key={page} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="min-h-0 flex-1 overflow-auto rounded-lg border border-border bg-panel p-4">
        <div className="min-h-full">
          <ActivePage />
        </div>
      </motion.main>
    </div>
  );
}
