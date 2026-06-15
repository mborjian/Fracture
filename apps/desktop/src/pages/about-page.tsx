import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { BrickWall, Bug, Copy, Download, GitFork, Handshake, Heart, Info, Loader2, QrCode, Star, X } from "lucide-react";
import { SiGithub, SiSolana, SiTether, SiTon } from "@icons-pack/react-simple-icons";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { ReleaseInfo, VersionInfo } from "@/types";

const DONATION_WALLETS = [
  {
    coin: "TON",
    network: undefined,
    walletId: "TON",
    address: "UQATECPeh89wITfWeFkUuO0o30Gup5QhmDlx9KWYNz54VCjN",
    qrImage: "/donate/TON.jpg",
    icon: { type: "simple-icon" as const, Component: SiTon, color: "#0088cc" },
  },
  {
    coin: "USDT",
    network: "TRC20",
    walletId: "USDT-TRC20",
    address: "TF24QUZmpznKqrnjN6GhawW45Nx1DBALtR",
    qrImage: "/donate/USDT.jpg",
    icon: { type: "simple-icon" as const, Component: SiTether, color: "#26a17b" },
  },
  {
    coin: "TRX",
    network: "TRC20",
    walletId: "TRX-TRC20",
    address: "TF24QUZmpznKqrnjN6GhawW45Nx1DBALtR",
    qrImage: "/donate/TRX.jpg",
    icon: { type: "image" as const, src: "/donate/tron-logo.png", alt: "TRX logo" },
  },
  {
    coin: "SOL",
    network: "Solana",
    walletId: "SOL-Solana",
    address: "5dGZcqQGECrczAtqfhrMn4A8VHLKR3qNx5Jaq8vAamyr",
    qrImage: "/donate/SOL.jpg",
    icon: { type: "simple-icon" as const, Component: SiSolana, color: "#9945ff" },
  },
] as const;

async function copyText(value: string, successMessage: string) {
  try {
    await navigator.clipboard.writeText(value);
    toast.success(successMessage);
  } catch (error) {
    toast.error((error as Error).message || "Copy failed");
  }
}

async function openExternalUrl(url: string) {
  try {
    await invoke("open_external_url", { url });
    return;
  } catch (_) {
    window.open(url, "_blank", "noopener,noreferrer");
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

function VersionRow({
  title,
  current,
  latest,
  busy,
  disabled,
  onCheck,
}: {
  title: string;
  current: string;
  latest?: string | null;
  busy: boolean;
  disabled?: boolean;
  onCheck: () => void;
}) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-xl border border-border bg-panel px-3 py-3">
      <div>
        <div className="text-sm font-medium">{title}</div>
        <div className="text-xs text-textMuted">
          {`Current ${current}`}
          {latest ? ` · Latest ${latest}` : ""}
        </div>
      </div>
      <Button variant="secondary" size="sm" onClick={onCheck} disabled={busy || disabled}>
        {busy ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <Download className="mr-1.5 h-4 w-4" />}
        Check For Update
      </Button>
    </div>
  );
}

export function AboutPage() {
  const [activeWalletId, setActiveWalletId] = useState<string | null>(null);
  const [versionInfo, setVersionInfo] = useState<VersionInfo | null>(null);
  const [appRelease, setAppRelease] = useState<ReleaseInfo | null>(null);
  const [loadingVersions, setLoadingVersions] = useState(true);
  const [checkingAppUpdate, setCheckingAppUpdate] = useState(false);
  const [updatingSingbox, setUpdatingSingbox] = useState(false);
  const activeWallet = DONATION_WALLETS.find((wallet) => wallet.walletId === activeWalletId) ?? null;

  useEffect(() => {
    let active = true;

    void (async () => {
      try {
        const [nextVersionInfo, nextAppRelease] = await Promise.all([
          api.versionInfo(),
          api.appReleaseInfo(),
        ]);
        if (!active) return;
        setVersionInfo(nextVersionInfo);
        setAppRelease(nextAppRelease);
      } catch (error) {
        if (active) {
          toast.error((error as Error).message);
        }
      } finally {
        if (active) {
          setLoadingVersions(false);
        }
      }
    })();

    return () => {
      active = false;
    };
  }, []);

  const handleAppUpdate = async () => {
    if (checkingAppUpdate) return;
    setCheckingAppUpdate(true);
    try {
      const release = await api.appReleaseInfo();
      setAppRelease(release);
      if (!release.updateAvailable) {
        toast.success(release.error ?? "Fracture is already up to date");
        return;
      }
      await api.openAppUpdatePage();
      toast.success(`Latest Fracture release: ${release.latestVersion}`);
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setCheckingAppUpdate(false);
    }
  };

  const handleSingboxUpdate = async () => {
    if (updatingSingbox) return;
    setUpdatingSingbox(true);
    try {
      const result = await api.updateSingbox();
      toast.success(result.message);
      const refreshed = await api.versionInfo();
      setVersionInfo(refreshed);
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setUpdatingSingbox(false);
    }
  };

  return (
    <>
      <div className="mt-4 space-y-3">
        <Card className="rounded-xl border border-border bg-panelAlt p-4 flex flex-col gap-3">
          <div className="flex items-center gap-2 text-sm font-semibold">
            <BrickWall className="h-4 w-4 text-blue-500" />
            <span>About</span>
          </div>
          <div className="text-sm text-textMuted">
            Fracture is a simple desktop control center for managing sing-box profiles, switching connections quickly, and keeping your local proxy setup easy to understand.
          </div>
          <div className="text-sm text-textMuted">
            It brings profiles, connection status, ports, and LAN sharing together in one place so your workflow stays fast, clean, and predictable.
          </div>
          <div className="text-sm text-textMuted">
            This app stands on the "SNI Spoofing" method as a core approach behind its connectivity workflow.
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-border bg-panelAlt px-3 py-2">
            <a
              href="https://github.com/mborjian/Fracture"
              target="_blank"
              rel="noreferrer"
              onClick={(event) => {
                event.preventDefault();
                void openExternalUrl("https://github.com/mborjian/Fracture");
              }}
              className="inline-flex items-center gap-2 text-sm text-textMuted transition-colors hover:text-text"
            >
              <SiGithub size={18} />
              <span>/mborjian/Fracture</span>
            </a>
            <div className="flex flex-wrap items-center justify-end gap-2">
              <a
                href="https://github.com/mborjian/Fracture/stargazers"
                target="_blank"
                rel="noreferrer"
                onClick={(event) => {
                  event.preventDefault();
                  void openExternalUrl("https://github.com/mborjian/Fracture/stargazers");
                }}
                className="inline-flex items-center gap-1 rounded-xl border border-[var(--tone-warning-border)] bg-[var(--tone-warning-bg)] px-2.5 py-1 text-xs font-medium text-[var(--tone-warning-text)] transition-colors hover:bg-[var(--tone-warning-bg-hover)]"
              >
                <Star className="h-3.5 w-3.5" />
                <span>Star</span>
              </a>
              <a
                href="https://github.com/mborjian/Fracture/subscription"
                target="_blank"
                rel="noreferrer"
                onClick={(event) => {
                  event.preventDefault();
                  void openExternalUrl("https://github.com/mborjian/Fracture/subscription");
                }}
                className="inline-flex items-center gap-1 rounded-xl border border-[var(--tone-success-border)] bg-[var(--tone-success-bg)] px-2.5 py-1 text-xs font-medium text-[var(--tone-success-text)] transition-colors hover:bg-[var(--tone-success-bg-hover)]"
              >
                <Heart className="h-3.5 w-3.5" />
                <span>Watch</span>
              </a>
              <a
                href="https://github.com/mborjian/Fracture/fork"
                target="_blank"
                rel="noreferrer"
                onClick={(event) => {
                  event.preventDefault();
                  void openExternalUrl("https://github.com/mborjian/Fracture/fork");
                }}
                className="inline-flex items-center gap-1 rounded-xl border border-[var(--tone-neutral-border)] bg-[var(--tone-neutral-bg)] px-2.5 py-1 text-xs font-medium text-[var(--tone-neutral-text)] transition-colors hover:bg-[var(--tone-neutral-bg-hover)]"
              >
                <GitFork className="h-3.5 w-3.5" />
                <span>Fork</span>
              </a>
              <a
                href="https://github.com/mborjian/Fracture/issues/new/choose"
                target="_blank"
                rel="noreferrer"
                onClick={(event) => {
                  event.preventDefault();
                  void openExternalUrl("https://github.com/mborjian/Fracture/issues/new/choose");
                }}
                className="inline-flex items-center gap-1 rounded-xl border border-[var(--tone-danger-border)] bg-[var(--tone-danger-bg)] px-2.5 py-1 text-xs font-medium text-[var(--tone-danger-text)] transition-colors hover:bg-[var(--tone-danger-bg-hover)]"
              >
                <Bug className="h-3.5 w-3.5" />
                <span>Report Issue</span>
              </a>
            </div>
          </div>
        </Card>

        <Card className="rounded-xl border border-border bg-panelAlt p-4 flex flex-col gap-3">
          <div className="flex items-center gap-2 text-sm font-semibold">
            <Handshake className="h-4 w-4 text-emerald-500" />
            <span>Thanks</span>
          </div>
          <div className="text-sm text-textMuted">
            Fracture stands on top of thoughtful open-source work. These projects helped shape the ideas, techniques, and building blocks behind the app.
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs text-textMuted">
            <a
              href="https://github.com/g3ntrix/Cloak"
              target="_blank"
              rel="noreferrer"
              onClick={(event) => {
                event.preventDefault();
                void openExternalUrl("https://github.com/g3ntrix/Cloak");
              }}
              className="inline-flex items-center gap-1 rounded-xl border border-border bg-panelAlt px-2 py-1 transition-colors hover:bg-panel hover:text-text"
            >
              <SiGithub size={12} />
              <span>/g3ntrix/Cloak</span>
            </a>
            <a
              href="https://github.com/patterniha/SNI-Spoofing"
              target="_blank"
              rel="noreferrer"
              onClick={(event) => {
                event.preventDefault();
                void openExternalUrl("https://github.com/patterniha/SNI-Spoofing");
              }}
              className="inline-flex items-center gap-1 rounded-xl border border-border bg-panelAlt px-2 py-1 transition-colors hover:bg-panel hover:text-text"
            >
              <SiGithub size={12} />
              <span>/patterniha/SNI-Spoofing</span>
            </a>
          </div>
        </Card>

        <Card className="rounded-xl border border-border bg-panelAlt p-4 flex flex-col gap-3">
          <div className="flex items-center gap-2 text-sm font-semibold">
            <Heart className="h-4 w-4 text-red-500" />
            <span>Support Fracture</span>
          </div>
          <div className="text-sm text-textMuted">
            If Fracture saved you time, made your setup easier, or helped you stay connected, a donation is a lovely way to support future updates.
          </div>
          <div className="space-y-2">
            {DONATION_WALLETS.map((wallet) => (
              <div
                key={wallet.walletId}
                className="flex justify-between gap-2 rounded-xl border border-border bg-panelAlt px-3 py-2 text-sm"
              >
                <div className="flex min-w-[100px] items-center gap-2 font-semibold">
                  {wallet.icon.type === "simple-icon" ? (
                    <wallet.icon.Component size={16} color={wallet.icon.color} className="shrink-0" />
                  ) : (
                    <img src={wallet.icon.src} alt={wallet.icon.alt} className="h-4 w-4 shrink-0" />
                  )}
                  <span>{wallet.coin}</span>
                  {wallet.network ? <span className="text-xs font-medium text-textMuted">{wallet.network}</span> : null}
                </div>
                <span className="min-w-0 text-center font-mono text-xs text-textMuted">{wallet.address}</span>
                <div className="flex items-center gap-1">
                  <CopyButton value={wallet.address} successMessage={`${wallet.coin} wallet copied`} />
                  <button
                    type="button"
                    className="inline-flex h-5 w-5 items-center justify-center rounded-full text-textMuted transition-colors hover:bg-panel hover:text-text"
                    title="Show QR"
                    onClick={() => setActiveWalletId(wallet.walletId)}
                  >
                    <QrCode className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </Card>

        <Card className="rounded-xl border border-border bg-panelAlt p-4 flex flex-col gap-3">
          <div className="flex items-center gap-2 text-sm font-semibold">
            <Info className="h-4 w-4 text-cyan-500" />
            <span>Versions</span>
          </div>
          <div className="space-y-2">
            <VersionRow
              title="Fracture"
              current={loadingVersions ? "Loading..." : (appRelease?.currentVersion ?? versionInfo?.appVersion ?? "unknown")}
              latest={appRelease?.latestVersion ?? null}
              busy={checkingAppUpdate}
              disabled={loadingVersions}
              onCheck={() => void handleAppUpdate()}
            />
            <VersionRow
              title="sing-box"
              current={loadingVersions ? "Loading..." : (versionInfo?.singboxVersion ?? "not found")}
              latest={versionInfo?.singboxRelease.latestVersion ?? null}
              busy={updatingSingbox}
              disabled={loadingVersions}
              onCheck={() => void handleSingboxUpdate()}
            />
          </div>
          {appRelease?.error ? <div className="text-xs text-textMuted">{appRelease.error}</div> : null}
          {versionInfo?.singboxRelease.error ? <div className="text-xs text-textMuted">{versionInfo.singboxRelease.error}</div> : null}
        </Card>
      </div>

      {activeWallet ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-overlaySoft p-4"
          role="button"
          tabIndex={0}
          onClick={() => setActiveWalletId(null)}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              setActiveWalletId(null);
            }
          }}
        >
          <div
            className="w-[280px] rounded-xl border border-border bg-panel p-3 shadow-soft"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="mb-2 flex items-center justify-between">
              <div className="text-sm font-semibold">
                {activeWallet.coin}
                {activeWallet.network ? <span className="ml-1 text-xs font-medium text-textMuted">{activeWallet.network}</span> : null}
              </div>
              <button
                type="button"
                className="inline-flex h-6 w-6 items-center justify-center rounded-full text-textMuted transition-colors hover:bg-panelAlt hover:text-text"
                title="Close"
                onClick={() => setActiveWalletId(null)}
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <img src={activeWallet.qrImage} alt={`${activeWallet.walletId} QR`} className="w-full rounded-xl border border-border" />
          </div>
        </div>
      ) : null}
    </>
  );
}
