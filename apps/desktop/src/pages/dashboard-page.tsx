import { motion } from "framer-motion";
import {
  ArrowDown,
  ArrowUp,
  Copy,
  LoaderCircle,
  Power,
  Square,
} from "lucide-react";
import { invoke } from "@tauri-apps/api/core";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useCoreStatusQuery, useProfilesQuery } from "@/hooks/useBackendQuery";
import { api } from "@/lib/api";
import { useAppStore } from "@/store/useAppStore";
type TrafficSample = { download: number; upload: number };
type ChartPoint = { x: number; y: number };

const TRAFFIC_HISTORY_LIMIT = 48;
const TRAFFIC_CHART_WIDTH = 720;
const TRAFFIC_CHART_HEIGHT = 46;
const TRAFFIC_CHART_PADDING = 10;
const TRAFFIC_DOWNLOAD_STROKE = "var(--color-success)";
const TRAFFIC_UPLOAD_STROKE = "var(--color-danger)";
const TRAFFIC_DOWNLOAD_FILL = "color-mix(in srgb, var(--color-success) 16%, transparent)";
const TRAFFIC_UPLOAD_FILL = "color-mix(in srgb, var(--color-danger) 14%, transparent)";

function formatSpeed(value: number) {
  if (value >= 1024 * 1024) {
    return `${(value / (1024 * 1024)).toFixed(2)} MB/s`;
  }
  return `${(value / 1024).toFixed(1)} KB/s`;
}

function formatData(value: number) {
  if (value >= 1024 * 1024 * 1024) {
    return `${(value / (1024 * 1024 * 1024)).toFixed(2)} GB`;
  }
  if (value >= 1024 * 1024) {
    return `${(value / (1024 * 1024)).toFixed(2)} MB`;
  }
  return `${Math.max(value / 1024, 0).toFixed(1)} KB`;
}

function formatUptime(totalSeconds: number) {
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return [hours, minutes, seconds].map((part) => String(part).padStart(2, "0")).join(":");
}

function buildTrafficPoints(values: number[], maxValue: number): ChartPoint[] {
  const innerHeight = TRAFFIC_CHART_HEIGHT - TRAFFIC_CHART_PADDING * 2;
  return values.map((value, index) => {
    const x = values.length > 1 ? (index / (values.length - 1)) * TRAFFIC_CHART_WIDTH : TRAFFIC_CHART_WIDTH / 2;
    const ratio = maxValue > 0 ? value / maxValue : 0;
    const y = TRAFFIC_CHART_HEIGHT - TRAFFIC_CHART_PADDING - ratio * innerHeight;
    return { x, y };
  });
}

function buildTrafficPath(points: ChartPoint[]) {
  if (points.length === 0) {
    return "";
  }
  if (points.length === 1) {
    const { x, y } = points[0];
    return `M ${x},${y}`;
  }

  let path = `M ${points[0].x},${points[0].y}`;
  for (let index = 0; index < points.length - 1; index += 1) {
    const current = points[index];
    const next = points[index + 1];
    const controlX = (current.x + next.x) / 2;
    path += ` C ${controlX},${current.y} ${controlX},${next.y} ${next.x},${next.y}`;
  }
  return path;
}

function buildTrafficArea(path: string, points: ChartPoint[]) {
  if (!path || points.length === 0) {
    return "";
  }
  const first = points[0];
  return [
    path,
    `L ${TRAFFIC_CHART_WIDTH},${TRAFFIC_CHART_HEIGHT}`,
    `L ${first.x},${TRAFFIC_CHART_HEIGHT}`,
    "Z",
  ].join(" ");
}

async function copyText(value: string, successMessage: string) {
  try {
    await navigator.clipboard.writeText(value);
    toast.success(successMessage);
  } catch (error) {
    toast.error((error as Error).message || "Copy failed");
  }
}

function CopyButton({ value, successMessage }: { value: string; successMessage: string }) {
  return (
    <button
      type="button"
      className="inline-flex h-5 w-5 items-center justify-center rounded-full text-textMuted transition-colors hover:bg-panel hover:text-text"
      onClick={() => void copyText(value, successMessage)}
      title="Copy"
    >
      <Copy className="h-3.5 w-3.5" />
    </button>
  );
}

async function ensureBackendAvailable() {
  try {
    await api.health();
    return;
  } catch {
    // In Tauri, the shell can restart the local backend if it was stopped.
  }

  await invoke("start_backend");
  await api.health();
}

export function DashboardPage() {
  const { data } = useCoreStatusQuery();
  const { data: profiles = [] } = useProfilesQuery();
  const setStatus = useAppStore((s) => s.setStatus);
  const status = useAppStore((s) => s.status);
  const addLog = useAppStore((s) => s.addLog);
  const pendingAction = useAppStore((s) => s.pendingConnectionAction);
  const setPendingAction = useAppStore((s) => s.setPendingConnectionAction);
  const [trafficHistory, setTrafficHistory] = useState<TrafficSample[]>(
    Array.from({ length: TRAFFIC_HISTORY_LIMIT }, () => ({ download: 0, upload: 0 }))
  );

  useEffect(() => {
    if (data) {
      setStatus(data);
    }
  }, [data, setStatus]);

  useEffect(() => {
    if (!status || status.state !== "running" || !status.activeProfileId) {
      return;
    }

    let cancelled = false;
    void api
      .refreshEgress()
      .then((next) => {
        if (!cancelled) {
          setStatus(next);
        }
      })
      .catch(() => {
        // Keep the current dashboard state if the egress refresh probe fails.
      });

    return () => {
      cancelled = true;
    };
  }, [setStatus, status?.activeProfileId, status?.state]);

  const state = status?.state ?? "stopped";
  const isConnected = state === "running";
  const isBusy = state === "starting" || pendingAction !== "idle";
  const activeProfile = useMemo(
    () => profiles.find((profile) => profile.id === status?.activeProfileId) ?? null,
    [profiles, status?.activeProfileId]
  );

  const activeProfileName = activeProfile?.name ?? "Not selected";
  const egressIp = status?.egressIp ?? "xx.xx.xx.xx";
  const egressMeta = `${egressIp}`;
  const localIp = status?.localDeviceIp ?? "--";
  const isLanSharing = status?.proxyScope === "lan";
  const isTunMode = status?.tunMode === true || status?.networkMode === "tun";
  const countryCode = (status?.egressCountry ?? "--").toUpperCase();
  const proxyHost = isLanSharing ? localIp : "127.0.0.1";
  const socksAddress = `${proxyHost}:${status?.socksPort ?? 2081}`;
  const httpAddress = `${proxyHost}:${status?.httpPort ?? 2080}`;
  const totalDownload = status?.sessionDownloadBytes ?? 0;
  const totalUpload = status?.sessionUploadBytes ?? 0;
  const totalData = totalDownload + totalUpload;
  const latencyValue = isConnected && typeof status?.latencyMs === "number" ? `${status.latencyMs} ms` : "--";
  const uptimeValue = isConnected ? formatUptime(status?.uptimeSeconds ?? 0) : "00:00:00";
  const currentDownloadBps = isConnected ? status?.downloadBps ?? 0 : 0;
  const currentUploadBps = isConnected ? status?.uploadBps ?? 0 : 0;

  useEffect(() => {
    if (!isConnected) {
      setTrafficHistory(Array.from({ length: TRAFFIC_HISTORY_LIMIT }, () => ({ download: 0, upload: 0 })));
      return;
    }

    const appendSample = () => {
      setTrafficHistory((previous) => {
        const next = [...previous, { download: currentDownloadBps, upload: currentUploadBps }];
        return next.slice(-TRAFFIC_HISTORY_LIMIT);
      });
    };

    appendSample();
    const intervalId = window.setInterval(appendSample, 1000);
    return () => {
      window.clearInterval(intervalId);
    };
  }, [currentDownloadBps, currentUploadBps, isConnected]);

  const downloadSeries = trafficHistory.map((point) => point.download);
  const uploadSeries = trafficHistory.map((point) => point.upload);
  const chartMax = Math.max(...downloadSeries, ...uploadSeries, 1);
  const downloadPoints = buildTrafficPoints(downloadSeries, chartMax);
  const uploadPoints = buildTrafficPoints(uploadSeries, chartMax);
  const downloadLine = buildTrafficPath(downloadPoints);
  const uploadLine = buildTrafficPath(uploadPoints);
  const downloadArea = buildTrafficArea(downloadLine, downloadPoints);
  const uploadArea = buildTrafficArea(uploadLine, uploadPoints);

  const toggle = async () => {
    try {
      if (isConnected || state === "starting") {
        void api.logUiEvent("debug", "Disconnect requested from dashboard", { source: "dashboard" }).catch(() => {
          addLog({
            id: `ui-disconnect-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
            ts: new Date().toISOString(),
            level: "debug",
            message: "Disconnect requested from dashboard",
            source: "dashboard"
          });
        });
        setPendingAction("disconnecting");
        const next = await api.stop();
        setStatus(next);
        void api.logUiEvent("info", "Disconnected from dashboard", { source: "dashboard" }).catch(() => {
          addLog({
            id: `ui-disconnect-info-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
            ts: new Date().toISOString(),
            level: "info",
            message: "Disconnected from dashboard",
            source: "dashboard"
          });
        });
        toast.success("Disconnected");
      } else {
        if (!status?.activeProfileId && profiles.length === 0) {
          toast.error("Import a profile first");
          setPendingAction("idle");
          return;
        }
        void api
          .logUiEvent(
            "debug",
            `Connect requested from dashboard using profile ${status?.activeProfileId ?? "auto"}`,
            { source: "dashboard" }
          )
          .catch(() => {
            addLog({
              id: `ui-connect-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
              ts: new Date().toISOString(),
              level: "debug",
              message: `Connect requested from dashboard using profile ${status?.activeProfileId ?? "auto"}`,
              source: "dashboard"
            });
          });
        setPendingAction("connecting");
        await api.cancelActiveTests().catch(() => undefined);
        await ensureBackendAvailable();
        const next = await api.start(status?.activeProfileId ?? null);
        if (next.state !== "running" || !next.ready) {
          throw new Error(next.lastError || "Connection failed to start");
        }
        setStatus(next);
        void api
          .logUiEvent(
            "info",
            `Connected from dashboard with ${next.runtime === "tcp-inject" ? "TCP injector" : "sing-box"}`,
            { source: "dashboard" }
          )
          .catch(() => {
            addLog({
              id: `ui-connect-info-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
              ts: new Date().toISOString(),
              level: "info",
              message: `Connected from dashboard with ${next.runtime === "tcp-inject" ? "TCP injector" : "sing-box"}`,
              source: "dashboard"
            });
          });
        const runtimeName = next.runtime === "tcp-inject" ? "TCP injector" : "sing-box";
        toast.success(`Connection started with ${runtimeName}`);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      void api
        .logUiEvent("error", `Connection failed: ${message}`, {
          source: "dashboard",
          trace: error instanceof Error ? error.stack ?? undefined : undefined
        })
        .catch(() => {
          addLog({
            id: `ui-connect-error-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
            ts: new Date().toISOString(),
            level: "error",
            message: `Connection failed: ${message}`,
            source: "dashboard",
            trace: error instanceof Error ? error.stack ?? undefined : undefined
          });
        });
      setPendingAction("idle");
      toast.error("Connection failed");
    }
  };

  const buttonIcon = pendingAction !== "idle" || state === "starting"
    ? <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />
    : isConnected
      ? <Square className="mr-2 h-4 w-4" />
      : <Power className="mr-2 h-4 w-4" />;

  const buttonLabel = pendingAction === "disconnecting"
    ? "Disconnecting..."
    : pendingAction === "connecting" || state === "starting"
      ? "Connecting..."
      : isConnected
        ? "Disconnect"
        : "Connect";

  return (
    <div className="flex min-h-full flex-col">
      <div className="space-y-4">
        <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}>
          <div className="flex gap-4">
            <Card className="rounded-xl border border-border bg-panelAlt p-4">
              <div className="flex flex-wrap items-center justify-start gap-3">
                <div className="text-xs text-textMuted">Network</div>
                <Badge label={isTunMode ? "Full Tunnel" : "Proxy Only"} tone={isTunMode ? "success" : "warning"} />
                <Badge label={status?.proxyScope === "lan" ? "LAN" : "Local"} tone={status?.proxyScope === "lan" ? "success" : "default"} />
              </div>
              <div className="mt-3 flex items-baseline gap-2">
                <span className="text-lg font-semibold tracking-[0.16em] text-text">{isLanSharing ? localIp : "127.0.0.1"}</span>
              </div>
              <div className="mt-3 flex gap-2 overflow-x-auto whitespace-nowrap">
                <span className="inline-flex shrink-0 items-center gap-2 rounded-full border border-border bg-panel px-3 py-1 text-xs">
                  <span className="font-semibold">SOCKS</span>
                  <span className="font-mono text-textMuted">{status?.socksPort ?? 2081}</span>
                  <CopyButton value={socksAddress} successMessage="SOCKS address copied" />
                </span>
                <span className="inline-flex shrink-0 items-center gap-2 rounded-full border border-border bg-panel px-3 py-1 text-xs">
                  <span className="font-semibold">HTTP</span>
                  <span className="font-mono text-textMuted">{status?.httpPort ?? 2080}</span>
                  <CopyButton value={httpAddress} successMessage="HTTP address copied" />
                </span>
              </div>
            </Card>

            <Card className="rounded-xl border border-border bg-panelAlt p-4 flex flex-1 flex-col">
              <div className="flex justify-between gap-3">
                <div>
                  <div className="flex items-center justify-start gap-3">
                    <div className="text-xs text-textMuted">Active profile</div>
                  </div>
                  <div className="mt-3 flex items-baseline gap-2">
                    <span className="text-lg font-semibold tracking-[0.16em] text-text">{activeProfileName}</span>
                  </div>
                </div>

                <div className="min-w-0">
                  <Button onClick={toggle} variant={isConnected ? "danger" : "default"}
                          className="h-11 min-w-[170px] rounded-xl" disabled={isBusy}>
                    {buttonIcon} {buttonLabel}
                  </Button>

                  {isConnected ? (
                    <div className="mt-3 flex items-center justify-between rounded-full border border-border px-3 py-1 text-xs">
                      <span className="font-semibold tracking-[0.28em] text-text">{egressMeta}</span>
                      <span className="uppercase tracking-[0.28em] text-textMuted">{countryCode}</span>
                    </div>
                  ) : null}
                </div>
              </div>

              {status?.lastError || (isConnected && !isTunMode) ? (
                <div className="mt-auto space-y-1 pt-2 text-xs">
                  {status?.lastError ? <div className="text-danger">{status.lastError}</div> : null}
                  {isConnected && !isTunMode ? (
                    <div className="text-warning">
                      Full system tunnel is off. Only proxy-aware apps will use this connection.
                    </div>
                  ) : null}
                </div>
              ) : null}
            </Card>
          </div>
        </motion.div>

        <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}>
          <Card className="rounded-xl border border-border bg-panelAlt p-4 space-y-4">
            <div className="flex items-start justify-between gap-4 text-xs text-textMuted">
              <div>
                <div>Delay</div>
                <div className="mt-1 text-sm font-semibold text-text">{latencyValue}</div>
              </div>
              <div className="text-right">
                <div>Uptime</div>
                <div className="mt-1 text-sm font-semibold text-text">{uptimeValue}</div>
              </div>
            </div>

            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
              <div className="rounded-xl border border-border bg-panelAlt p-4">
                <div className="flex items-center gap-2 text-lg font-semibold">
                  <ArrowDown className="h-4 w-4 text-success" />
                  <span>{formatSpeed(currentDownloadBps)}</span>
                </div>
                <div className="mt-1 text-xs text-textMuted">
                  Down <span className="mx-1">•</span> {formatData(totalDownload)}
                </div>
              </div>

              <div className="rounded-xl border border-border bg-panelAlt p-4">
                <div className="flex items-center gap-2 text-lg font-semibold">
                  <ArrowUp className="h-4 w-4 text-danger" />
                  <span>{formatSpeed(currentUploadBps)}</span>
                </div>
                <div className="mt-1 text-xs text-textMuted">
                  Up <span className="mx-1">•</span> {formatData(totalUpload)}
                </div>
              </div>

              <div className="rounded-xl border border-border bg-panelAlt p-4">
                <div className="text-lg font-semibold">{formatData(totalData)}</div>
                <div className="mt-1 text-xs text-textMuted">Total</div>
              </div>
            </div>

            <div className="rounded-xl border border-border bg-panel px-3 py-3">
              <div className="overflow-hidden rounded-lg">
                <svg
                  viewBox={`0 0 ${TRAFFIC_CHART_WIDTH} ${TRAFFIC_CHART_HEIGHT}`}
                  className="h-12 w-full"
                  preserveAspectRatio="none"
                  aria-label="Upload and download speed chart"
                  role="img"
                >
                  <line
                    x1="0"
                    y1={TRAFFIC_CHART_HEIGHT - 0.5}
                    x2={TRAFFIC_CHART_WIDTH}
                    y2={TRAFFIC_CHART_HEIGHT - 0.5}
                    className="stroke-border"
                    strokeWidth="1"
                  />
                  {downloadLine ? (
                    <>
                      <path d={downloadArea} fill={TRAFFIC_DOWNLOAD_FILL} />
                      <path
                        d={downloadLine}
                        fill="none"
                        stroke={TRAFFIC_DOWNLOAD_STROKE}
                        strokeOpacity="0.85"
                        strokeWidth="1.5"
                        strokeLinejoin="round"
                        strokeLinecap="round"
                      />
                    </>
                  ) : null}
                  {uploadLine ? (
                    <>
                      <path d={uploadArea} fill={TRAFFIC_UPLOAD_FILL} />
                      <path
                        d={uploadLine}
                        fill="none"
                        stroke={TRAFFIC_UPLOAD_STROKE}
                        strokeOpacity="0.8"
                        strokeWidth="1.5"
                        strokeLinejoin="round"
                        strokeLinecap="round"
                      />
                    </>
                  ) : null}
                </svg>
              </div>
            </div>
          </Card>
        </motion.div>
      </div>

    </div>
  );
}
