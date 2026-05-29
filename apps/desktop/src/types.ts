export type NavPage =
  | "dashboard"
  | "about"
  | "profiles"
  | "settings"
  | "logs";

export type ConnectionState = "stopped" | "starting" | "running" | "error";

export interface CoreStatus {
  state: ConnectionState;
  runtime: "sing-box" | null;
  activeProfileId: string | null;
  uptimeSeconds: number;
  latencyMs: number | null;
  downloadBps: number;
  uploadBps: number;
  sessionDownloadBytes: number;
  sessionUploadBytes: number;
  restarts: number;
  ready: boolean;
  egressIp: string | null;
  egressCountry: string | null;
  localDeviceIp: string | null;
  proxyScope: "local" | "lan";
  listenHost?: string;
  httpPort: number;
  socksPort: number;
  lastError: string | null;
}

export type ProfileProtocol = "vless" | "vmess" | "trojan" | "shadowsocks";

export interface Profile {
  id: string;
  name: string;
  protocol: ProfileProtocol;
  server: string;
  port: number;
  group: string;
  link: string;
  lastPingMs: number | null;
  lastPingAt: string | null;
  lastSpeedMbps: number | null;
  lastSpeedAt: string | null;
  pingSuccessCount: number;
  pingFailCount: number;
}

export interface ProfileImportResult {
  ok: boolean;
  imported: number;
  created: number;
  updated: number;
  errors: string[];
}

export interface CloudflareListener {
  id: string;
  LISTEN_HOST: string;
  LISTEN_PORT: number;
  CONNECT_IP: string;
  CONNECT_PORT: number;
  FAKE_SNI: string;
}

export interface CloudflareConfig {
  selectedId: string;
  selected: CloudflareListener;
  listeners: CloudflareListener[];
}

export interface RoutingConfig {
  dnsServers: string;
  dohUrl: string;
  fakeIpCidr: string;
  bypassDomains: string;
  routingRules: string;
  tunMode: boolean;
  tunReason: string;
  outboundMode: string;
}

export interface CoreSettings {
  proxyScope: "local" | "lan";
  proxyPort: number;
  socksPort: number;
  autoReconnect: boolean;
}

export interface UiSettings {
  theme: "light" | "dark" | "system";
  updateChannel: string;
  runOnStartup: boolean;
  closeToTray: boolean;
}

export interface LogEvent {
  id: string;
  ts: string;
  level: "debug" | "info" | "warning" | "error";
  message: string;
}

export interface WsEvent<T = unknown> {
  type: string;
  payload: T;
}

export type ProbeMode = "quick" | "full";
