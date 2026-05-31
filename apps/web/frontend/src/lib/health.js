// System health for the status bar (修复 A1 · 五容器拓扑).
// Polls the web-backend aggregate /health/deps every 30s. Gated on a token so
// the public error pages (404/500/403) that also render <StatusBar/> don't fire
// an unauthenticated request (which the api interceptor would bounce to /login).
import { useQuery } from '@tanstack/react-query';
import { endpoints, tokenStore } from './api';

export function useSystemHealth() {
  return useQuery({
    queryKey: ['system-health'],
    queryFn: endpoints.healthDeps,
    enabled: !!tokenStore.get(),
    refetchInterval: 30_000,
    staleTime: 20_000,
    retry: 1,
  });
}
