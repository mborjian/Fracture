import { useEffect, useMemo, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { useQueryClient } from "@tanstack/react-query";
import { Check, ChevronDown, Plus, Save, Trash2, Laptop, Network } from "lucide-react";
import { toast } from "sonner";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Switch } from "@/components/ui/switch"
import { useCloudflareConfigQuery, useCoreSettingsQuery, useRoutingConfigQuery, useUiSettingsQuery } from "@/hooks/useBackendQuery";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import type { CloudflareConfig, CloudflareListener, CoreSettings, RoutingConfig, UiSettings } from "@/types";

const DEFAULT_CORE: CoreSettings = {
  proxyScope: "local",
  proxyPort: 2080,
  socksPort: 2081,
  autoReconnect: true,
  transportMode: "tcp-inject"
};

const DEFAULT_UI: UiSettings = {
  theme: "system",
  updateChannel: "stable",
  runOnStartup: false,
  closeToTray: true,
  showDevelopmentLogs: false
};

const DEFAULT_ROUTING: RoutingConfig = {
  dnsServers: "1.1.1.1,8.8.8.8",
  dohUrl: "https://dns.google/dns-query",
  fakeIpCidr: "198.18.0.0/15",
  bypassDomains: "*.lan,*.local,*.msftconnecttest.com",
  routingRules: "geoip:private -> direct\ngeosite:ads -> block",
  tunMode: true,
  tunReason: "TUN mode uses sing-box and may require Administrator privileges on Windows.",
  outboundMode: "tun"
};

const DEFAULT_LISTENER: CloudflareListener = {
  id: "listener-default",
  CONNECT_IP: "",
  FAKE_SNI: "",
};

const DEFAULT_CLOUDFLARE: CloudflareConfig = {
  selectedId: DEFAULT_LISTENER.id,
  selected: DEFAULT_LISTENER,
  listeners: [DEFAULT_LISTENER]
};

function formatListenerLabel(listener: CloudflareListener) {
  const modeLabel = listener.CONNECT_IP.trim() || "No Connect IP";
  return {
    title: listener.FAKE_SNI.trim() || "No Fake SNI",
    subtitle: modeLabel
  };
}

function normalizeConnectIp(value: string) {
  const host = value.trim();
  if (!host) {
    return "";
  }
  if (host.includes(":")) {
    throw new Error("CONNECT IP should not include a port");
  }
  return host;
}

function applyTheme(theme: UiSettings["theme"]) {
  const resolved = theme === "system"
    ? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
    : theme;
  document.documentElement.setAttribute("data-theme", resolved);
  localStorage.setItem("fracture-ui-theme", theme);
}

export function SettingsPage() {
  const queryClient = useQueryClient();
  const { data: cloudflareData, refetch: refetchCloudflare } = useCloudflareConfigQuery();
  const { data: coreData, refetch: refetchCore } = useCoreSettingsQuery();
  const { data: routingData, refetch: refetchRouting } = useRoutingConfigQuery();
  const { data: uiData, refetch: refetchUi } = useUiSettingsQuery();

  const [cloudflareDraft, setCloudflareDraft] = useState<CloudflareConfig>(DEFAULT_CLOUDFLARE);
  const [connectEndpoint, setConnectEndpoint] = useState("");
  const [fakeSniDraft, setFakeSniDraft] = useState("");
  const [listenerOpen, setListenerOpen] = useState(false);
  const [coreDraft, setCoreDraft] = useState<CoreSettings>(DEFAULT_CORE);
  const [routingDraft, setRoutingDraft] = useState<RoutingConfig>(DEFAULT_ROUTING);
  const [uiDraft, setUiDraft] = useState<UiSettings>(DEFAULT_UI);
  const [savingProxy, setSavingProxy] = useState(false);
  const [savingRouting, setSavingRouting] = useState(false);
  const [savingJson, setSavingJson] = useState(false);
  const listenerMenuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (cloudflareData) {
      setCloudflareDraft(cloudflareData);
      setConnectEndpoint(cloudflareData.selected.CONNECT_IP);
      setFakeSniDraft(cloudflareData.selected.FAKE_SNI);
    }
  }, [cloudflareData]);

  useEffect(() => {
    if (coreData) {
      setCoreDraft({
        proxyScope: coreData.proxyScope === "lan" ? "lan" : "local",
        proxyPort: coreData.proxyPort,
        socksPort: coreData.socksPort,
        autoReconnect: coreData.autoReconnect,
        transportMode:coreData.transportMode
      });
    }
  }, [coreData]);

  useEffect(() => {
    if (routingData) {
      setRoutingDraft({
        ...DEFAULT_ROUTING,
        ...routingData,
        outboundMode: routingData.tunMode ? "tun" : "proxy"
      });
    }
  }, [routingData]);

  useEffect(() => {
    if (uiData) {
      const nextUi = { ...DEFAULT_UI, ...uiData };
      setUiDraft(nextUi);
      applyTheme(nextUi.theme);
    }
  }, [uiData]);

  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      if (!listenerMenuRef.current?.contains(event.target as Node)) {
        setListenerOpen(false);
      }
    };
    window.addEventListener("pointerdown", onPointerDown);
    return () => window.removeEventListener("pointerdown", onPointerDown);
  }, []);

  const selectedListener = useMemo(
    () => cloudflareDraft.listeners.find((item) => item.id === cloudflareDraft.selectedId) ?? cloudflareDraft.listeners[0] ?? DEFAULT_LISTENER,
    [cloudflareDraft]
  );

  const listenerDirty = useMemo(() => {
    const currentEndpoint = selectedListener.CONNECT_IP.trim();
    return connectEndpoint.trim() !== currentEndpoint || fakeSniDraft !== selectedListener.FAKE_SNI;
  }, [connectEndpoint, fakeSniDraft, selectedListener]);

  const persistCloudflare = async (nextDraft: CloudflareConfig, successMessage?: string) => {
    const saved = await api.saveCloudflareConfig(nextDraft);
    setCloudflareDraft(saved);
    setConnectEndpoint(saved.selected.CONNECT_IP);
    setFakeSniDraft(saved.selected.FAKE_SNI);
    await refetchCloudflare();
    if (successMessage) {
      toast.success(successMessage);
    }
    return saved;
  };

  const selectListener = async (listenerId: string) => {
    const nextSelected = cloudflareDraft.listeners.find((item) => item.id === listenerId);
    if (!nextSelected) return;
    const nextDraft: CloudflareConfig = {
      ...cloudflareDraft,
      selectedId: listenerId,
      selected: nextSelected
    };
    setCloudflareDraft(nextDraft);
    setConnectEndpoint(nextSelected.CONNECT_IP);
    setFakeSniDraft(nextSelected.FAKE_SNI);
    setListenerOpen(false);
    try {
      await persistCloudflare(nextDraft);
    } catch (error) {
      toast.error((error as Error).message);
    }
  };

  const addListener = async () => {
    const id = `listener-${Date.now()}`;
    const nextListener: CloudflareListener = { ...DEFAULT_LISTENER, id };
    const nextDraft: CloudflareConfig = {
      ...cloudflareDraft,
      selectedId: id,
      selected: nextListener,
      listeners: [...cloudflareDraft.listeners, nextListener]
    };
    setCloudflareDraft(nextDraft);
    setConnectEndpoint("");
    setFakeSniDraft("");
    try {
      await persistCloudflare(nextDraft, "Listener added");
      setListenerOpen(false);
    } catch (error) {
      toast.error((error as Error).message);
    }
  };

  const saveListener = async () => {
    if (savingJson || !listenerDirty) return;
    setSavingJson(true);
    try {
      const CONNECT_IP = normalizeConnectIp(connectEndpoint);
      const updated: CloudflareListener = {
        ...selectedListener,
        CONNECT_IP,
        FAKE_SNI: fakeSniDraft.trim()
      };
      const nextDraft: CloudflareConfig = {
        ...cloudflareDraft,
        selectedId: selectedListener.id,
        selected: updated,
        listeners: cloudflareDraft.listeners.map((item) => (item.id === selectedListener.id ? updated : item))
      };
      await persistCloudflare(nextDraft, "Listener saved");
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setSavingJson(false);
    }
  };

  const removeSelectedListener = async () => {
    if (cloudflareDraft.listeners.length <= 1) {
      return;
    }
    const remaining = cloudflareDraft.listeners.filter((item) => item.id !== selectedListener.id);
    const nextSelected = remaining[0];
    if (!nextSelected) {
      return;
    }
    const nextDraft: CloudflareConfig = {
      ...cloudflareDraft,
      selectedId: nextSelected.id,
      selected: nextSelected,
      listeners: remaining
    };
    setCloudflareDraft(nextDraft);
    setConnectEndpoint(nextSelected.CONNECT_IP);
    setFakeSniDraft(nextSelected.FAKE_SNI);
    try {
      await persistCloudflare(nextDraft, "Listener removed");
    } catch (error) {
      toast.error((error as Error).message);
    }
  };

  const saveProxySettings = async (nextCore: CoreSettings) => {
    setCoreDraft(nextCore);
    if (savingProxy) {
      setCoreDraft(nextCore);
      return;
    }
    setSavingProxy(true);
    try {
      const saved = await api.saveCoreSettings(nextCore);
      setCoreDraft(saved);
      await refetchCore();
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setSavingProxy(false);
    }
  };

  const saveRoutingSettings = async (nextRouting: RoutingConfig) => {
    setRoutingDraft(nextRouting);
    if (savingRouting) {
      return;
    }
    setSavingRouting(true);
    try {
      const saved = await api.saveRoutingConfig(nextRouting);
      const normalized = {
        ...DEFAULT_ROUTING,
        ...saved,
        outboundMode: saved.tunMode ? "tun" : "proxy"
      };
      setRoutingDraft(normalized);
      queryClient.setQueryData(["routing-config"], normalized);
      await refetchRouting();
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setSavingRouting(false);
    }
  };

  const changeTheme = async (theme: UiSettings["theme"]) => {
    const next = { ...uiDraft, theme };
    setUiDraft(next);
    applyTheme(theme);
    try {
      const saved = await api.saveUiSettings(next);
      queryClient.setQueryData(["ui-settings"], saved);
      await invoke("apply_shell_settings");
      await refetchUi();
    } catch (error) {
      toast.error((error as Error).message);
    }
  };

  const updateUiSettings = async (patch: Partial<UiSettings>) => {
    const next = { ...uiDraft, ...patch };
    setUiDraft(next);
    if (patch.theme) {
      applyTheme(next.theme);
    }
    try {
      const saved = await api.saveUiSettings(next);
      queryClient.setQueryData(["ui-settings"], saved);
      await invoke("apply_shell_settings");
      await refetchUi();
    } catch (error) {
      toast.error((error as Error).message);
    }
  };

  const selectedMeta = formatListenerLabel(selectedListener);

  return (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="col-span-2">
              <Card className="h-[330px] rounded-xl">
              <h3 className="text-sm font-semibold mb-3">Appearance</h3>

              <ToggleGroup type="single" className="mx-auto w-[270px] mb-3" value={uiDraft.theme}
                           onValueChange={(value) => {
                             if (value) { void changeTheme(value as "light" | "dark" | "system"); }
                           }}
              >
                <ToggleGroupItem value="light">Light</ToggleGroupItem>
                <ToggleGroupItem value="dark">Dark</ToggleGroupItem>
                <ToggleGroupItem value="system">System</ToggleGroupItem>
              </ToggleGroup>

              <label className="flex items-center justify-between gap-3 rounded-xl border border-border bg-panelAlt px-4 py-3 mb-3 text-sm">
                <div>
                  <div className="font-medium">Start with Windows</div>
                  <div className="text-xs text-textMuted">
                    Launch Fracture automatically when you sign in.
                  </div>
                </div>

                <Switch
                  checked={uiDraft.runOnStartup}
                  onCheckedChange={(checked) =>
                    void updateUiSettings({
                      runOnStartup: checked
                    })
                  }
                />
              </label>

              <label className="flex items-center justify-between gap-3 rounded-xl border border-border bg-panelAlt px-4 py-3 text-sm">
                <div>
                  <div className="font-medium">Close To Tray</div>
                  <div className="text-xs text-textMuted">
                    When enabled, closing the window hides Fracture to the system tray.
                  </div>
                </div>

                <Switch
                  checked={uiDraft.closeToTray}
                  onCheckedChange={(checked) =>
                    void updateUiSettings({
                      closeToTray: checked
                    })
                  }
                />
              </label>

              <label className="mt-3 flex items-center justify-between gap-3 rounded-xl border border-border bg-panelAlt px-4 py-3 text-sm">
                <div>
                  <div className="font-medium">Show Development Logs</div>
                  <div className="text-xs text-textMuted">
                    Show verbose debug entries in the Logs tab.
                  </div>
                </div>

                <Switch
                  checked={uiDraft.showDevelopmentLogs}
                  onCheckedChange={(checked) =>
                    void updateUiSettings({
                      showDevelopmentLogs: checked
                    })
                  }
                />
              </label>
            </Card>
            </div>

            <div className="col-span-1">
              <Card className="flex h-[340px] flex-col rounded-xl">
              <h3 className="mb-3 text-sm font-semibold">Cloudflare Listener</h3>
              <div className="mb-3 flex items-center gap-2">
                <div ref={listenerMenuRef} className="relative min-w-0 flex-1">
                  <button
                    type="button"
                    onClick={() => setListenerOpen((prev) => !prev)}
                    className="flex h-9 w-full items-center justify-between rounded-xl border border-border bg-panelAlt px-3 text-left"
                  >
                    <div className="min-w-0">
                      <div className="truncate text-sm">{selectedMeta.title} <span className="text-[12px] text-textMuted">({selectedMeta.subtitle})</span></div>
                    </div>
                    <ChevronDown className={cn("ml-2 h-4 w-4 shrink-0 transition-transform", listenerOpen ? "rotate-180" : "")} />
                  </button>
                  {listenerOpen ? (
                    <div className="absolute left-0 right-0 top-11 z-20 max-h-56 overflow-auto rounded-xl border border-border bg-panel shadow-soft">
                      {cloudflareDraft.listeners.map((listener) => {
                        const meta = formatListenerLabel(listener);
                        const active = listener.id === cloudflareDraft.selectedId;
                        return (
                          <button
                            key={listener.id}
                            type="button"
                            onClick={() => void selectListener(listener.id)}
                            className={cn(
                              "flex h-9 w-full items-center justify-between px-3 text-left hover:bg-panelAlt",
                              active ? "bg-panelAlt" : ""
                            )}
                          >
                            <div className="min-w-0">
                              <div className="truncate text-sm">{meta.title} <span className="text-[12px] text-textMuted">({meta.subtitle})</span></div>
                            </div>
                            {active ? <Check className="ml-2 h-4 w-4 shrink-0 text-accent" /> : null}
                          </button>
                        );
                      })}
                    </div>
                  ) : null}
                </div>
                <Button variant="secondary" size="sm" className="h-9 w-9 rounded-xl px-0" onClick={() => void addListener()} title="Add listener">
                  <Plus className="h-4 w-4" />
                </Button>
              </div>

                <div className="grid gap-3 mt-3">
                  <label className="space-y-1 text-sm">
                    <div className="text-xs text-textMuted">Connect IP</div>
                    <Input
                      value={connectEndpoint}
                      onChange={(event) => setConnectEndpoint(event.target.value)}
                      placeholder="104.19.229.21"
                      spellCheck={false}
                    />
                  </label>
                  <label className="space-y-1 text-sm">
                    <div className="text-xs text-textMuted">Fake SNI</div>
                    <Input
                      value={fakeSniDraft}
                      onChange={(event) => setFakeSniDraft(event.target.value)}
                      placeholder="hcaptcha.com"
                      spellCheck={false}
                    />
                  </label>
                  <div className="flex items-center justify-end gap-2 pt-1">
                    {cloudflareDraft.listeners.length > 1 ? (
                      <Button
                        size="sm"
                        variant="secondary"
                        className="h-8 w-8 rounded-full px-0"
                        onClick={() => void removeSelectedListener()}
                        title="Remove listener"
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    ) : null}
                    {listenerDirty ? (
                      <Button
                        size="sm"
                        className="h-8 w-8 rounded-full px-0"
                        onClick={() => void saveListener()}
                        disabled={savingJson}
                        title="Save listener"
                      >
                        {savingJson ? <Check className="h-4 w-4" /> : <Save className="h-4 w-4" />}
                      </Button>
                    ) : null}
                  </div>
                </div>
            </Card>
            </div>

            <div className="col-span-1">
              <Card className="flex h-[340px] flex-col rounded-xl">
              <h3 className="mb-4 text-sm font-semibold">Network Mode</h3>
              <ToggleGroup type="single" value={coreDraft.proxyScope} className="w-[180px] mx-auto mb-5"
                onValueChange={(value) => {
                  if (value) {
                    const nextScope = value as "local" | "lan";
                    void saveProxySettings({
                      ...coreDraft,
                      proxyScope: nextScope,
                    });
                    if (nextScope === "lan" && routingDraft.tunMode) {
                      void saveRoutingSettings({
                        ...routingDraft,
                        tunMode: false,
                        outboundMode: "proxy"
                      });
                      toast.info("LAN proxy sharing turned off Full System Tunnel so only one whole-device mode stays active.");
                    }
                  }
                }}
              >
                <ToggleGroupItem value="local" className="gap-2"><Laptop className="h-4 w-4" /> Local</ToggleGroupItem>
                <ToggleGroupItem value="lan" className="gap-2"><Network className="h-4 w-4" /> LAN</ToggleGroupItem>
              </ToggleGroup>

              <label className="mb-4 flex items-center justify-between gap-3 rounded-xl border border-border bg-panelAlt px-4 py-3 text-sm">
                <div>
                  <div className="font-medium text-text">Full System Tunnel</div>
                  <div className="text-xs text-textMuted">
                    Routes Windows traffic through sing-box TUN instead of relying only on app proxy support.
                  </div>
                  {coreDraft.proxyScope === "lan" ? (
                    <div className="mt-1 text-xs text-warning">
                      Turning this on will switch proxy sharing back to Local.
                    </div>
                  ) : null}
                </div>

                <Switch
                  checked={routingDraft.tunMode}
                  disabled={savingRouting}
                  onCheckedChange={(checked) => {
                    void saveRoutingSettings({
                      ...routingDraft,
                      tunMode: checked,
                      outboundMode: checked ? "tun" : "proxy"
                    });
                    if (checked && coreDraft.proxyScope === "lan") {
                      void saveProxySettings({
                        ...coreDraft,
                        proxyScope: "local"
                      });
                      toast.info("Full System Tunnel switched proxy sharing back to Local so only one whole-device mode stays active.");
                    }
                  }}
                />
              </label>

              <label className="space-y-1 text-sm mb-4">
                  <div className="text-xs text-textMuted">HTTP</div>
                  <Input
                    type="number"
                    value={coreDraft.proxyPort}
                    onChange={(e) => void saveProxySettings({ ...coreDraft, proxyPort: Number(e.target.value) || 0 })}
                    placeholder="HTTP"
                  />
                </label>
              <label className="space-y-1 text-sm">
                  <div className="text-xs text-textMuted">SOCKS</div>
                  <Input
                    type="number"
                    value={coreDraft.socksPort}
                    onChange={(e) => void saveProxySettings({ ...coreDraft, socksPort: Number(e.target.value) || 0 })}
                    placeholder="SOCKS"
                  />
                </label>
            </Card>
            </div>

            <div className="col-span-1">
              <Card className="flex h-[330px] flex-col rounded-xl">
              <h3 className="mb-4 text-sm font-semibold">Transport Mode</h3>
              <ToggleGroup
                type="single"
                value={coreDraft.transportMode || "tcp-inject"}
                onValueChange={(value) => {
                  if (value) void saveProxySettings({ ...coreDraft, transportMode: value as "singbox" | "tcp-inject" });
                }}
                className="w-full"
              >
                <ToggleGroupItem value="tcp-inject" className="flex-1">TCP Inject (Fake TLS)</ToggleGroupItem>
                <ToggleGroupItem value="singbox" className="flex-1">Standard (sing‑box)</ToggleGroupItem>
              </ToggleGroup>
              <p className="text-xs text-textMuted mt-2">
                TCP Inject mode uses wrong_seq packet injection to bypass DPI. Requires administrator privileges.
              </p>

                <label className="mt-10 flex items-center justify-between gap-3 rounded-xl border border-border bg-panelAlt px-4 py-3 text-sm">
                <div>
                  <div className="font-medium text-text">Auto Reconnect</div>
                  <div className="text-xs text-textMuted">
                    Auto-reconnect on changes
                  </div>
                </div>

                <Switch
                  checked={coreDraft.autoReconnect}
                  onCheckedChange={(checked) =>
                    void saveProxySettings({
                      ...coreDraft,
                      autoReconnect: checked
                    })
                  }
                />
              </label>
            </Card>
            </div>
          </div>
        </div>
  );
}
