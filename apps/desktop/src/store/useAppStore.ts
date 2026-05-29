import { create } from "zustand";
import type { ConnectionState, CoreStatus, LogEvent, NavPage } from "@/types";

interface AppState {
  page: NavPage;
  connectionState: ConnectionState;
  status: CoreStatus | null;
  logs: LogEvent[];
  setPage: (page: NavPage) => void;
  setStatus: (status: CoreStatus) => void;
  addLog: (log: LogEvent) => void;
  clearLogs: () => void;
}

export const useAppStore = create<AppState>((set) => ({
  page: "dashboard",
  connectionState: "stopped",
  status: null,
  logs: [],
  setPage: (page) => set({ page }),
  setStatus: (status) =>
    set({
      status,
      connectionState: status.state
    }),
  addLog: (log) =>
    set((state) => ({
      logs: [log, ...state.logs].slice(0, 1500)
    })),
  clearLogs: () => set({ logs: [] })
}));
