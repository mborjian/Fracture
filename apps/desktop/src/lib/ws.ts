import { toast } from "sonner";
import type { QueryClient } from "@tanstack/react-query";
import type { CoreStatus, LogEvent, WsEvent } from "@/types";
import { useAppStore } from "@/store/useAppStore";

const WS_URL = "ws://127.0.0.1:8765/ws/events";

export function connectRealtime(queryClient: QueryClient) {
  let reconnectTimer: number | null = null;
  let ws: WebSocket | null = null;
  let disposed = false;
  let announcedReady = false;

  const scheduleReconnect = () => {
    if (disposed || reconnectTimer !== null) {
      return;
    }
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = null;
      open();
    }, 2000);
  };

  const open = () => {
    if (disposed) {
      return;
    }

    const socket = new WebSocket(WS_URL);
    ws = socket;

    socket.onopen = () => {
      if (disposed || ws !== socket) {
        socket.close();
        return;
      }
      if (!announcedReady) {
        toast.success("Realtime channel connected");
        announcedReady = true;
      }
    };

    socket.onmessage = (event) => {
      if (disposed || ws !== socket) {
        return;
      }
      const data = JSON.parse(event.data) as WsEvent;
      if (data.type === "status") {
        useAppStore.getState().setStatus(data.payload as CoreStatus);
      }
      if (data.type === "log") {
        useAppStore.getState().addLog(data.payload as LogEvent);
      }
      if (data.type === "ping") {
        const ping = data.payload as { profileId: string; ok: boolean; latencyMs: number | null; error?: string; at?: string };
        queryClient.setQueryData(["profiles"], (profiles: unknown) => {
          if (!Array.isArray(profiles)) return profiles;
          return profiles.map((profile) => {
            const item = profile as Record<string, unknown>;
            return item && item.id === ping.profileId
              ? { ...item, lastPingMs: typeof ping.latencyMs === "number" ? ping.latencyMs : -1, lastPingAt: ping.at ?? null }
              : profile;
          });
        });
        window.dispatchEvent(new CustomEvent("fracture-profile-metric", { detail: { type: "ping", ...ping } }));
        useAppStore.getState().addLog({
          id: `ping-${ping.profileId}-${Date.now()}`,
          ts: new Date().toISOString(),
          level: ping.ok ? "info" : "warning",
          message: ping.ok
            ? `Delay ${ping.profileId}: ${ping.latencyMs} ms`
            : `Delay ${ping.profileId} failed: ${ping.error ?? "unknown error"}`
        });
      }
      if (data.type === "ping-summary") {
        const summary = data.payload as { completed: number; successes: number; failures: number; cancelled: boolean };
        window.dispatchEvent(new CustomEvent("fracture-profile-metric-summary", { detail: { type: "ping", ...summary } }));
        useAppStore.getState().addLog({
          id: `ping-summary-${Date.now()}`,
          ts: new Date().toISOString(),
          level: "info",
          message: `Delay test finished: ${summary.successes}/${summary.completed} ok, ${summary.failures} failed${summary.cancelled ? " (cancelled)" : ""}`
        });
      }
      if (data.type === "speed") {
        const speed = data.payload as { profileId: string; ok: boolean; speedMBps: number | null; error?: string; at?: string };
        queryClient.setQueryData(["profiles"], (profiles: unknown) => {
          if (!Array.isArray(profiles)) return profiles;
          return profiles.map((profile) => {
            const item = profile as Record<string, unknown>;
            return item && item.id === speed.profileId
              ? { ...item, lastSpeedMbps: typeof speed.speedMBps === "number" ? speed.speedMBps : 0, lastSpeedAt: speed.at ?? null }
              : profile;
          });
        });
        window.dispatchEvent(new CustomEvent("fracture-profile-metric", { detail: { type: "speed", ...speed } }));
        useAppStore.getState().addLog({
          id: `speed-${speed.profileId}-${Date.now()}`,
          ts: new Date().toISOString(),
          level: speed.ok ? "info" : "warning",
          message: speed.ok
            ? `Speed ${speed.profileId}: ${speed.speedMBps} MB/s`
            : `Speed ${speed.profileId} failed: ${speed.error ?? "unknown error"}`
        });
      }
      if (data.type === "speed-summary") {
        const summary = data.payload as { completed: number; successes: number; failures: number; cancelled: boolean };
        window.dispatchEvent(new CustomEvent("fracture-profile-metric-summary", { detail: { type: "speed", ...summary } }));
        useAppStore.getState().addLog({
          id: `speed-summary-${Date.now()}`,
          ts: new Date().toISOString(),
          level: "info",
          message: `Speed test finished: ${summary.successes}/${summary.completed} ok, ${summary.failures} failed${summary.cancelled ? " (cancelled)" : ""}`
        });
      }
    };

    socket.onclose = () => {
      if (ws === socket) {
        ws = null;
      }
      scheduleReconnect();
    };

    socket.onerror = () => {
      if (ws === socket) {
        socket.close();
      }
    };
  };

  open();

  return () => {
    disposed = true;
    if (reconnectTimer !== null) {
      window.clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (ws) {
      ws.onclose = null;
      ws.onerror = null;
      ws.onmessage = null;
      ws.close();
      ws = null;
    }
  };
}
