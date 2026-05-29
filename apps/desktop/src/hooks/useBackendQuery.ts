import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export function useBackendHealth() {
  return useQuery({
    queryKey: ["backend-health"],
    queryFn: api.health,
    refetchInterval: 5000
  });
}

export function useCoreStatusQuery() {
  return useQuery({
    queryKey: ["core-status"],
    queryFn: api.status,
    refetchInterval: 3000
  });
}

export function useProfilesQuery() {
  return useQuery({
    queryKey: ["profiles"],
    queryFn: api.profiles,
    refetchInterval: 5000
  });
}

export function useCloudflareConfigQuery() {
  return useQuery({
    queryKey: ["cloudflare-config"],
    queryFn: api.cloudflareConfig
  });
}

export function useRoutingConfigQuery() {
  return useQuery({
    queryKey: ["routing-config"],
    queryFn: api.routingConfig
  });
}

export function useCoreSettingsQuery() {
  return useQuery({
    queryKey: ["core-settings"],
    queryFn: api.coreSettings
  });
}

export function useUiSettingsQuery() {
  return useQuery({
    queryKey: ["ui-settings"],
    queryFn: api.uiSettings
  });
}

export function useTunnelSupportQuery() {
  return useQuery({
    queryKey: ["tunnel-support"],
    queryFn: api.tunnelSupport
  });
}
