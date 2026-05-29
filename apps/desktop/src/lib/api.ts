import type {
  CloudflareConfig,
  CoreSettings,
  CoreStatus,
  Profile,
  ProfileImportResult,
  ProbeMode,
  RoutingConfig,
  UiSettings
} from "@/types";

const BASE_URL = "http://127.0.0.1:8765";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    }
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `API ${response.status} ${response.statusText}`);
  }

  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return (await response.json()) as T;
  }

  return (await response.text()) as T;
}

export const api = {
  health: () => request<{ ok: boolean; version: string }>("/health"),
  status: () => request<CoreStatus>("/api/core/status"),
  start: (profileId?: string | null) =>
    request<CoreStatus>("/api/core/start", {
      method: "POST",
      body: JSON.stringify({ profile_id: profileId ?? null })
    }),
  stop: () =>
    request<CoreStatus>("/api/core/stop", {
      method: "POST"
    }),
  restart: () =>
    request<CoreStatus>("/api/core/restart", {
      method: "POST"
    }),
  refreshEgress: () =>
    request<CoreStatus>("/api/core/egress/refresh", {
      method: "POST"
    }),

  profiles: () => request<Profile[]>("/api/profiles"),
  activeProfile: () => request<{ activeProfileId: string | null }>("/api/profiles/active"),
  setActiveProfile: (profileId: string) =>
    request<{ ok: boolean; activeProfileId: string | null }>("/api/profiles/active", {
      method: "POST",
      body: JSON.stringify({ profile_id: profileId })
    }),
  renameProfile: (profileId: string, name: string) =>
    request<{ ok: boolean }>(`/api/profiles/${profileId}/rename`, {
      method: "POST",
      body: JSON.stringify({ name })
    }),
  pingProfile: (profileId: string, timeoutMs = 8000, mode: ProbeMode = "quick") =>
    request<{ profileId: string; ok: boolean; latencyMs: number | null; error?: string; at: string }>(
      `/api/profiles/${profileId}/ping`,
      {
        method: "POST",
        body: JSON.stringify({ timeout_ms: timeoutMs, mode })
      }
    ),
  speedProfile: (profileId: string, timeoutMs = 15000, mode: ProbeMode = "quick") =>
    request<{ profileId: string; ok: boolean; speedMBps: number | null; error?: string; at: string }>(
      `/api/profiles/${profileId}/speed`,
      {
        method: "POST",
        body: JSON.stringify({ timeout_ms: timeoutMs, mode })
      }
    ),
  exportProfile: (profileId: string) =>
    request<{ link: string }>(`/api/profiles/${profileId}/export`),
  reorderProfiles: (profileIds: string[]) =>
    request<{ ok: boolean; reordered: number }>("/api/profiles/order", {
      method: "POST",
      body: JSON.stringify({ profile_ids: profileIds })
    }),
  sortProfilesByPing: () =>
    request<{ ok: boolean; reordered: number }>("/api/profiles/sort-by-ping", {
      method: "POST"
    }),
  sortProfilesBySpeed: () =>
    request<{ ok: boolean; reordered: number }>("/api/profiles/sort-by-speed", {
      method: "POST"
    }),
  deleteProfile: (profileId: string) =>
    request<{ ok: boolean; removed: number }>(`/api/profiles/${profileId}`, {
      method: "DELETE"
    }),
  cleanupFailedProfiles: () =>
    request<{ ok: boolean; removed: number }>("/api/profiles/cleanup/failed", {
      method: "POST"
    }),
  importProfiles: (text: string) =>
    request<ProfileImportResult>("/api/profiles/import", {
      method: "POST",
      body: JSON.stringify({ text })
    }),
  pingAllProfiles: (profileIds?: string[], timeoutMs = 8000, mode: ProbeMode = "quick") =>
    request<{ running: boolean; completed: number; successes: number; failures: number; cancelled: boolean }>(
      "/api/profiles/ping-all",
      {
        method: "POST",
        body: JSON.stringify({ profile_ids: profileIds ?? null, timeout_ms: timeoutMs, mode })
      }
    ),
  speedAllProfiles: (profileIds?: string[], timeoutMs = 15000, mode: ProbeMode = "quick") =>
    request<{ running: boolean; completed: number; successes: number; failures: number; cancelled: boolean }>(
      "/api/profiles/speed-all",
      {
        method: "POST",
        body: JSON.stringify({ profile_ids: profileIds ?? null, timeout_ms: timeoutMs, mode })
      }
    ),
  cancelPingAll: () =>
    request<{ ok: boolean; message?: string }>("/api/profiles/ping-all/cancel", {
      method: "POST"
    }),

  cloudflareConfig: () => request<CloudflareConfig>("/api/settings/cloudflare"),
  saveCloudflareConfig: (payload: CloudflareConfig) =>
    request<CloudflareConfig>("/api/settings/cloudflare", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  routingConfig: () => request<RoutingConfig>("/api/settings/routing"),
  saveRoutingConfig: (payload: RoutingConfig) =>
    request<RoutingConfig>("/api/settings/routing", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  coreSettings: () => request<CoreSettings>("/api/settings/core"),
  saveCoreSettings: (payload: CoreSettings) =>
    request<CoreSettings>("/api/settings/core", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  uiSettings: () => request<UiSettings>("/api/settings/ui"),
  saveUiSettings: (payload: UiSettings) =>
    request<UiSettings>("/api/settings/ui", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  tunnelSupport: () => request<{ supported: boolean; reason: string }>("/api/settings/tunnel-support")
};
