import { ArrowUpDown, Gauge, Import, Loader2, TimerReset, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import { createPortal } from "react-dom";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useProfilesQuery } from "@/hooks/useBackendQuery";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useAppStore } from "@/store/useAppStore";
import type { ProbeMode, Profile } from "@/types";

type ContextMenuState = {
  x: number;
  y: number;
  profile: Profile;
};

type MetricOverrides = Record<string, number | null | undefined>;

const CONTEXT_MENU_WIDTH = 180;
const CONTEXT_MENU_HEIGHT = 234;
const VIEWPORT_PADDING = 8;

function moveItem<T>(items: T[], from: number, to: number): T[] {
  const next = [...items];
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}

function clampToViewport(value: number, size: number, viewportSize: number) {
  return Math.max(VIEWPORT_PADDING, Math.min(value, viewportSize - size - VIEWPORT_PADDING));
}

export function ProfilesPage() {
  const { data = [] } = useProfilesQuery();
  const queryClient = useQueryClient();
  const status = useAppStore((s) => s.status);
  const setStatus = useAppStore((s) => s.setStatus);
  const [orderedProfiles, setOrderedProfiles] = useState<Profile[]>([]);
  const [importText, setImportText] = useState("");
  const [showImport, setShowImport] = useState(false);
  const [importDragActive, setImportDragActive] = useState(false);
  const [importBusy, setImportBusy] = useState(false);
  const [pingAllRunning, setPingAllRunning] = useState(false);
  const [speedAllRunning, setSpeedAllRunning] = useState(false);
  const [sortBusy, setSortBusy] = useState(false);
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
  const [menuBusy, setMenuBusy] = useState(false);
  const [renaming, setRenaming] = useState<Profile | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const [dropIndex, setDropIndex] = useState<number | null>(null);
  const [testingPingIds, setTestingPingIds] = useState<Set<string>>(new Set());
  const [testingSpeedIds, setTestingSpeedIds] = useState<Set<string>>(new Set());
  const [pingOverrides, setPingOverrides] = useState<MetricOverrides>({});
  const [speedOverrides, setSpeedOverrides] = useState<MetricOverrides>({});
  const [probeMode, setProbeMode] = useState<ProbeMode>("quick");
  const dragDepthRef = useRef(0);
  const importTextAreaRef = useRef<HTMLTextAreaElement | null>(null);
  const ensuringSelectionRef = useRef(false);
  const activeProfileId = status?.activeProfileId ?? null;

  useEffect(() => {
    setOrderedProfiles(data);
  }, [data]);

  useEffect(() => {
    const onPointerDown = () => setContextMenu(null);
    const onEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (showImport) {
          setShowImport(false);
          setImportDragActive(false);
          dragDepthRef.current = 0;
          return;
        }
        setContextMenu(null);
      }
    };
    window.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onEscape);
    window.addEventListener("scroll", onPointerDown, true);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onEscape);
      window.removeEventListener("scroll", onPointerDown, true);
    };
  }, [showImport]);

  useEffect(() => {
    if (data.length === 0 || activeProfileId || ensuringSelectionRef.current) {
      return;
    }
    ensuringSelectionRef.current = true;
    void (async () => {
      try {
        await api.setActiveProfile(data[0].id);
        const latestStatus = await api.status();
        setStatus(latestStatus);
      } catch (error) {
        toast.error((error as Error).message);
      } finally {
        ensuringSelectionRef.current = false;
      }
    })();
  }, [activeProfileId, data, setStatus]);

  useEffect(() => {
    if (!showImport) {
      setImportDragActive(false);
      dragDepthRef.current = 0;
      return;
    }

    const frame = window.requestAnimationFrame(() => {
      importTextAreaRef.current?.focus();
    });

    return () => window.cancelAnimationFrame(frame);
  }, [showImport]);

  const refreshProfiles = async () => {
    await queryClient.refetchQueries({ queryKey: ["profiles"], type: "active" });
  };

  const replaceImportText = (content: string) => {
    setImportText(content);
  };

  const isAllowedImportFile = (file: File) => file.name.toLowerCase().endsWith(".txt");

  const handleImportDrop = async (event: DragEvent<HTMLElement>) => {
    event.preventDefault();
    event.stopPropagation();
    dragDepthRef.current = 0;
    setImportDragActive(false);

    const files = Array.from(event.dataTransfer.files);
    if (files.length > 0) {
      if (files.some((file) => !isAllowedImportFile(file))) {
        toast.error("Only .txt files can be dropped here");
        return;
      }
      try {
        const payloads = await Promise.all(files.map((file) => file.text()));
        replaceImportText(payloads.join("\n"));
        toast.success(`${payloads.length} file content${payloads.length === 1 ? "" : "s"} loaded`);
      } catch (error) {
        toast.error((error as Error).message);
      }
      return;
    }

    const droppedText = event.dataTransfer.getData("text/plain");
    if (droppedText.trim()) {
      replaceImportText(droppedText);
    }
  };

  const handleImport = async () => {
    if (importBusy) return;
    setImportBusy(true);
    try {
      const result = await api.importProfiles(importText);
      await refreshProfiles();
      setImportText("");
      setShowImport(false);
      toast.success(`Imported ${result.imported} profiles (${result.created} created, ${result.updated} updated)`);
      if (result.errors.length > 0) {
        toast.error(`${result.errors.length} lines failed to parse`);
      }
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setImportBusy(false);
    }
  };

  const handleSelectActive = async (profileId: string) => {
    try {
      await api.setActiveProfile(profileId);
      const latestStatus = await api.status();
      setStatus(latestStatus);
      toast.success("Active profile updated");
    } catch (error) {
      toast.error((error as Error).message);
    }
  };

  const handleDeleteOne = async (profileId: string) => {
    if (profileId === activeProfileId) {
      toast.error("Selected profile cannot be deleted");
      return;
    }
    if (!window.confirm("Delete this profile?")) return;
    try {
      await api.deleteProfile(profileId);
      await refreshProfiles();
      toast.success("Profile deleted");
    } catch (error) {
      toast.error((error as Error).message);
    }
  };

  const handleDeleteFailed = async () => {
    if (!window.confirm("Delete profiles with no successful ping?")) return;
    try {
      const result = await api.cleanupFailedProfiles();
      await refreshProfiles();
      toast.success(`Deleted ${result.removed} failed profiles`);
    } catch (error) {
      toast.error((error as Error).message);
    }
  };

  const handlePingAll = async () => {
    if (pingAllRunning) return;
    const ids = orderedProfiles.map((profile) => profile.id);
    setPingAllRunning(true);
    setTestingPingIds(new Set(ids));
    setPingOverrides(Object.fromEntries(ids.map((id) => [id, null])));
    try {
      const effectiveResult = await api.pingAllProfiles(ids, probeMode === "quick" ? 2500 : 6000, probeMode);
      await refreshProfiles();
      setPingOverrides({});
      toast.success(`${probeMode === "quick" ? "Quick" : "Full"} delay test done: ${effectiveResult.successes} ok, ${effectiveResult.failures} failed`);
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setPingAllRunning(false);
      setTestingPingIds(new Set());
    }
  };

  const handleSortByPing = async () => {
    if (sortBusy) return;
    setSortBusy(true);
    try {
      const result = await api.sortProfilesByPing();
      await refreshProfiles();
      toast.success(result.reordered > 0 ? "Profiles reordered by delay" : "Profiles already sorted by delay");
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setSortBusy(false);
    }
  };

  const handleSortBySpeed = async () => {
    if (sortBusy) return;
    setSortBusy(true);
    try {
      const result = await api.sortProfilesBySpeed();
      await refreshProfiles();
      toast.success(result.reordered > 0 ? "Profiles reordered by speed" : "Profiles already sorted by speed");
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setSortBusy(false);
    }
  };

  const handleSpeedAll = async () => {
    if (speedAllRunning) return;
    const ids = orderedProfiles.map((profile) => profile.id);
    setSpeedAllRunning(true);
    setTestingSpeedIds(new Set(ids));
    setSpeedOverrides(Object.fromEntries(ids.map((id) => [id, null])));
    try {
      const result = await api.speedAllProfiles(ids, probeMode === "quick" ? 2500 : 7000, probeMode);
      await refreshProfiles();
      setSpeedOverrides({});
      toast.success(`${probeMode === "quick" ? "Quick" : "Full"} speed test done: ${result.successes} ok, ${result.failures} failed`);
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setSpeedAllRunning(false);
      setTestingSpeedIds(new Set());
    }
  };

  const saveOrder = async (nextOrder: Profile[]) => {
    try {
      await api.reorderProfiles(nextOrder.map((item) => item.id));
      queryClient.setQueryData(["profiles"], nextOrder);
    } catch (error) {
      toast.error((error as Error).message);
      setOrderedProfiles(data);
    }
  };

  const startRename = (profile: Profile) => {
    setContextMenu(null);
    setRenaming(profile);
    setRenameValue(profile.name);
  };

  const doRename = async () => {
    if (!renaming) return;
    const value = renameValue.trim();
    if (!value) {
      toast.error("Name is required");
      return;
    }
    try {
      await api.renameProfile(renaming.id, value);
      await refreshProfiles();
      setRenaming(null);
      toast.success("Profile renamed");
    } catch (error) {
      toast.error((error as Error).message);
    }
  };

  const runContextAction = async (action: "rename" | "ping" | "speed" | "export" | "delete") => {
    if (!contextMenu || menuBusy) return;
    const profile = contextMenu.profile;
    if (action === "rename") {
      startRename(profile);
      return;
    }
    if (action === "delete") {
      setContextMenu(null);
      await handleDeleteOne(profile.id);
      return;
    }

    setMenuBusy(true);
    try {
      if (action === "ping") {
        setTestingPingIds(new Set([profile.id]));
        setPingOverrides((prev) => ({ ...prev, [profile.id]: null }));
        const result = await api.pingProfile(profile.id, probeMode === "quick" ? 2500 : 6000, probeMode);
        setPingOverrides((prev) => ({ ...prev, [profile.id]: result.latencyMs }));
        toast.success(result.ok ? `${probeMode === "quick" ? "Quick" : "Full"} delay: ${result.latencyMs} ms` : `Delay failed: ${result.error ?? "unknown error"}`);
      } else if (action === "speed") {
        setTestingSpeedIds(new Set([profile.id]));
        setSpeedOverrides((prev) => ({ ...prev, [profile.id]: null }));
        const result = await api.speedProfile(profile.id, probeMode === "quick" ? 2500 : 7000, probeMode);
        setSpeedOverrides((prev) => ({ ...prev, [profile.id]: result.speedMBps ?? 0 }));
        toast.success(result.ok ? `${probeMode === "quick" ? "Quick" : "Full"} speed: ${result.speedMBps} MB/s` : `Speed test failed: ${result.error ?? "unknown error"}`);
      } else if (action === "export") {
        const link = profile.link?.trim() ? profile.link : (await api.exportProfile(profile.id)).link;
        await navigator.clipboard.writeText(link);
        toast.success("Profile config copied to clipboard");
      }
      await refreshProfiles();
      setContextMenu(null);
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setTestingPingIds((prev) => {
        const next = new Set(prev);
        next.delete(profile.id);
        return next;
      });
      setTestingSpeedIds((prev) => {
        const next = new Set(prev);
        next.delete(profile.id);
        return next;
      });
      setMenuBusy(false);
    }
  };

  const handleDropAtIndex = async (targetIndex: number) => {
    if (!draggingId) return;
    const fromIndex = orderedProfiles.findIndex((item) => item.id === draggingId);
    if (fromIndex < 0) return;
    const normalizedIndex = targetIndex > fromIndex ? targetIndex - 1 : targetIndex;
    if (normalizedIndex === fromIndex) {
      setDraggingId(null);
      setDropIndex(null);
      return;
    }
    const next = moveItem(orderedProfiles, fromIndex, normalizedIndex);
    setOrderedProfiles(next);
    setDraggingId(null);
    setDropIndex(null);
    await saveOrder(next);
  };

  const handleProfileDragOver = (event: DragEvent<HTMLElement>, index: number) => {
    if (!draggingId) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    const bounds = event.currentTarget.getBoundingClientRect();
    const targetIndex = event.clientY > bounds.top + bounds.height / 2 ? index + 1 : index;
    setDropIndex(targetIndex);
  };

  const metricBadgeClass = useMemo(
    () =>
      "inline-flex h-7 min-w-[72px] shrink-0 items-center justify-between rounded-full border border-border bg-panelAlt/80 px-2 text-[11px] font-medium text-textMuted backdrop-blur-sm",
    []
  );

  const typeBadgeClass =
    "inline-flex h-7 min-w-[72px] shrink-0 items-center justify-center rounded-full border border-border bg-panelAlt/80 px-2 text-[11px] font-semibold uppercase text-textMuted backdrop-blur-sm";

  const sortButtonClass = "h-7 rounded-full px-2.5 text-[11px]";

  const closeImportPanel = () => {
    setShowImport(false);
    setImportDragActive(false);
    dragDepthRef.current = 0;
  };

  return (
    <div className="bg-panel">
      <div className="sticky -top-4 z-20 -mx-4 px-4 pt-4 bg-panel flex flex-wrap items-center gap-2 border-b border-border/60 pb-3 mb-2">
        <div className="inline-flex h-9 items-center rounded-full border border-border bg-panelAlt p-1 text-xs">
          <button
            type="button"
            className={cn("rounded-full px-3 py-1 transition-colors", probeMode === "quick" ? "bg-accent text-white" : "text-textMuted")}
            onClick={() => setProbeMode("quick")}
          >
            Quick
          </button>
          <button
            type="button"
            className={cn("rounded-full px-3 py-1 transition-colors", probeMode === "full" ? "bg-accent text-white" : "text-textMuted")}
            onClick={() => setProbeMode("full")}
          >
            Full
          </button>
        </div>
        <Button variant="secondary" size="sm" className="text-xs" onClick={() => void handlePingAll()} disabled={pingAllRunning}>
          {pingAllRunning ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <TimerReset className="mr-1.5 h-4 w-4" />}
          {probeMode === "quick" ? "Quick Delay" : "Full Delay"}
        </Button>
        <Button variant="secondary" size="sm" className="text-xs" onClick={() => void handleSpeedAll()} disabled={speedAllRunning}>
          {speedAllRunning ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <Gauge className="mr-1.5 h-4 w-4" />}
          {probeMode === "quick" ? "Quick Speed" : "Full Speed"}
        </Button>
        <Button variant="secondary" size="sm" className="text-xs" onClick={() => void handleDeleteFailed()}>
          <Trash2 className="mr-1.5 h-4 w-4" />
          Remove Failed
        </Button>
        <Button variant="secondary" size="sm" className={sortButtonClass} onClick={() => void handleSortByPing()} disabled={sortBusy}>
          {sortBusy ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <ArrowUpDown className="mr-1 h-3.5 w-3.5" />}
          Delay
        </Button>
        <Button variant="secondary" size="sm" className={sortButtonClass} onClick={() => void handleSortBySpeed()} disabled={sortBusy}>
          {sortBusy ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <ArrowUpDown className="mr-1 h-3.5 w-3.5" />}
          Speed
        </Button>
        <Button
          variant="secondary"
          size="sm"
          className="ml-auto"
          onClick={() => setShowImport((prev) => !prev)}
          aria-expanded={showImport}
        >
          <Import className="mr-1.5 h-4 w-4" />
          Import
        </Button>
      </div>

      {showImport ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-overlay p-4 backdrop-blur-sm"
          onMouseDown={closeImportPanel}
        >
          <Card
            className="relative w-full max-w-4xl overflow-hidden rounded-xl border border-border bg-panel shadow-2xl"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-border px-4 py-3">
              <div>
                <div className="text-sm font-semibold text-text">Import Profiles</div>
              </div>
              <Button variant="ghost" size="sm" className="h-8 w-8 p-0" onClick={closeImportPanel} aria-label="Close import panel">
                <X className="h-4 w-4" />
              </Button>
            </div>
            <div className="p-4">
              <div
                className={cn(
                  "relative min-h-[20rem] overflow-hidden rounded-xl border border-dashed border-border bg-panelAlt transition-colors",
                  importDragActive ? "border-accent/70 bg-importDrag" : ""
                )}
                onDragEnter={(event) => {
                  event.preventDefault();
                  dragDepthRef.current += 1;
                  setImportDragActive(true);
                }}
                onDragOver={(event) => {
                  event.preventDefault();
                  event.dataTransfer.dropEffect = "copy";
                  setImportDragActive(true);
                }}
                onDragLeave={(event) => {
                  event.preventDefault();
                  dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
                  if (dragDepthRef.current === 0) {
                    setImportDragActive(false);
                  }
                }}
                onDrop={(event) => void handleImportDrop(event)}
              >
                <textarea
                  ref={importTextAreaRef}
                  className={cn(
                    "absolute rounded-xl inset-0 h-full w-full resize-none bg-transparent px-5 py-5 text-sm text-text outline-none",
                    importDragActive ? "pointer-events-none" : ""
                  )}
                  aria-label="Import profiles text"
                  value={importText}
                  onChange={(event) => setImportText(event.target.value)}
                />
                {importText.trim().length === 0 ? (
                  <div className="pointer-events-none absolute inset-0 flex items-center justify-center px-8 text-center">
                    <div className="max-w-md text-lg font-medium leading-snug text-textMuted/90">
                      {importDragActive ? "Drop the text file here" : "Paste profile text here or drop a text file into this area"}
                    </div>
                  </div>
                ) : null}
              </div>
              <div className="mt-4 flex justify-end">
                <Button size="sm" onClick={() => void handleImport()} disabled={importBusy}>
                  {importBusy ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : null}
                  Import Profiles
                </Button>
              </div>
            </div>
          </Card>
        </div>
      ) : null}

      <div className="space-y-2">
        {orderedProfiles.map((profile, index) => {
          const active = activeProfileId === profile.id;
          const pingDisplay = testingPingIds.has(profile.id)
            ? "--"
            : typeof pingOverrides[profile.id] === "number"
              ? String(pingOverrides[profile.id])
              : typeof profile.lastPingMs === "number"
                ? String(profile.lastPingMs)
                : "--";
          const speedDisplay = testingSpeedIds.has(profile.id)
            ? "--"
            : typeof speedOverrides[profile.id] === "number"
              ? Number(speedOverrides[profile.id]).toFixed(2)
              : typeof profile.lastSpeedMbps === "number"
                ? profile.lastSpeedMbps.toFixed(2)
                : "--";
          const pingDanger = pingDisplay === "-1";
          const profileType = String(profile.protocol ?? "unknown").toUpperCase();
          return (
            <div key={profile.id}>
              <div
                className={cn(
                  "h-2 rounded-full transition-colors",
                  dropIndex === index ? "bg-accent/70" : "bg-transparent"
                )}
                onDragOver={(event) => {
                  event.preventDefault();
                  if (draggingId) {
                    setDropIndex(index);
                  }
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                  void handleDropAtIndex(index);
                }}
              />
              <Card
                draggable
                onDragStart={(event) => {
                  setDraggingId(profile.id);
                  setDropIndex(index);
                  event.dataTransfer.effectAllowed = "move";
                  event.dataTransfer.setData("text/plain", profile.id);
                }}
                onDragEnd={() => {
                  setDraggingId(null);
                  setDropIndex(null);
                }}
                onDragOver={(event) => handleProfileDragOver(event, index)}
                onDrop={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                  void handleDropAtIndex(dropIndex ?? index);
                }}
                className={cn(
                  "w-full rounded-xl cursor-move p-3 transition-all",
                  active
                    ? "border-success bg-[linear-gradient(135deg,rgba(34,197,94,0.10)_0%,rgba(34,197,94,0.05)_45%,rgba(255,255,255,0.05)_100%)] shadow-[0_0_0_1px_rgba(34,197,94,0.4),0_0_26px_rgba(34,197,94,0.18)]"
                    : "",
                  draggingId === profile.id ? "opacity-80" : ""
                )}
                onClick={() => void handleSelectActive(profile.id)}
                onContextMenu={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                  setContextMenu({
                    x: event.clientX,
                    y: event.clientY,
                    profile
                  });
                }}
              >
                <div className="flex items-center gap-3">
                  <span className={typeBadgeClass}>{profileType}</span>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-semibold">{profile.name}</div>
                    <div className="truncate text-xs text-textMuted">
                      {String(profile.server ?? "")}:{String(profile.port ?? "")}
                    </div>
                  </div>
                  <span className={metricBadgeClass}>
                    <span className={pingDanger ? "text-danger" : ""}>{pingDisplay}</span>
                    <span className="text-[10px] text-textMuted/90">ms</span>
                  </span>
                  <span className={metricBadgeClass}>
                    <span>{speedDisplay}</span>
                    <span className="text-[10px] text-textMuted/90">MB/s</span>
                  </span>
                </div>
              </Card>
            </div>
          );
        })}
        <div
          className={cn(
            "h-2 rounded-full transition-colors",
            dropIndex === orderedProfiles.length ? "bg-accent/70" : "bg-transparent"
          )}
          onDragOver={(event) => {
            event.preventDefault();
            if (draggingId) {
              setDropIndex(orderedProfiles.length);
            }
          }}
          onDrop={(event) => {
            event.preventDefault();
            event.stopPropagation();
            void handleDropAtIndex(orderedProfiles.length);
          }}
        />
      </div>

      {contextMenu ? createPortal(
        <div
          className="fixed z-50 min-w-[180px] rounded-xl border border-border bg-panel p-1 shadow-soft"
          style={{
            left: clampToViewport(contextMenu.x, CONTEXT_MENU_WIDTH, window.innerWidth),
            top: clampToViewport(contextMenu.y, CONTEXT_MENU_HEIGHT, window.innerHeight)
          }}
          onPointerDown={(event) => event.stopPropagation()}
        >
          <button className="flex h-8 w-full items-center rounded px-2 text-left text-sm hover:bg-panelAlt" onClick={() => void runContextAction("rename")}>
            Rename
          </button>
          <button className="flex h-8 w-full items-center rounded px-2 text-left text-sm hover:bg-panelAlt" onClick={() => void runContextAction("ping")}>
            Real Delay
          </button>
          <button className="flex h-8 w-full items-center rounded px-2 text-left text-sm hover:bg-panelAlt" onClick={() => void runContextAction("speed")}>
            Speed Test
          </button>
          <button className="flex h-8 w-full items-center rounded px-2 text-left text-sm hover:bg-panelAlt" onClick={() => void runContextAction("export")}>
            Export
          </button>
          <div className="my-1 h-px bg-border" />
          <button
            className="flex h-8 w-full items-center rounded px-2 text-left text-sm text-danger hover:bg-panelAlt disabled:cursor-not-allowed disabled:opacity-50"
            onClick={() => void runContextAction("delete")}
            disabled={contextMenu.profile.id === activeProfileId}
          >
            Delete
          </button>
        </div>,
        document.body
      ) : null}

      {renaming ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-overlaySoft p-4">
          <Card className="w-full max-w-[420px] rounded-xl space-y-3">
            <div className="text-sm font-semibold">Rename Profile</div>
            <Input value={renameValue} onChange={(event) => setRenameValue(event.target.value)} placeholder="Profile name" />
            <div className="flex justify-end gap-2">
              <Button variant="secondary" size="sm" onClick={() => setRenaming(null)}>
                Cancel
              </Button>
              <Button size="sm" onClick={() => void doRename()}>
                Save
              </Button>
            </div>
          </Card>
        </div>
      ) : null}
    </div>
  );
}
