import { create } from "zustand";
import type { ConnectionState, CoreStatus, LogEvent, NavPage, PendingConnectionAction } from "@/types";

interface AppState {
  page: NavPage;
  connectionState: ConnectionState;
  pendingConnectionAction: PendingConnectionAction;
  status: CoreStatus | null;
  pingAllRunning: boolean;
  speedAllRunning: boolean;
  logs: LogEvent[];
  setPage: (page: NavPage) => void;
  setPendingConnectionAction: (action: PendingConnectionAction) => void;
  setStatus: (status: CoreStatus) => void;
  setPingAllRunning: (running: boolean) => void;
  setSpeedAllRunning: (running: boolean) => void;
  addLog: (log: LogEvent) => void;
  clearLogs: () => void;
}

export const useAppStore = create<AppState>((set, get) => ({
  page: "dashboard",
  connectionState: "stopped",
  pendingConnectionAction: "idle",
  status: null,
  pingAllRunning: false,
  speedAllRunning: false,
  logs: [],
  setPage: (page) => set({ page }),
  setPendingConnectionAction: (pendingConnectionAction) => set({ pendingConnectionAction }),
  setStatus: (status) =>
    set({
      status,
      connectionState: status.state,
      pendingConnectionAction:
        get().pendingConnectionAction === "connecting" && (status.state === "running" || status.state === "error" || status.state === "stopped")
          ? "idle"
          : get().pendingConnectionAction === "disconnecting" && status.state === "stopped"
            ? "idle"
            : get().pendingConnectionAction
    }),
  setPingAllRunning: (pingAllRunning) => set({ pingAllRunning }),
  setSpeedAllRunning: (speedAllRunning) => set({ speedAllRunning }),
  addLog: (log) => {
    if (get().logs.some((entry) => entry.id === log.id)) {
      return;
    }
    set((state) => ({
      logs: [log, ...state.logs].slice(0, 1500)
    }));
  },
  clearLogs: () => set({ logs: [] })
}));
