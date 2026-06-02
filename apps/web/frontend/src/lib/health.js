// System health for the status bar + right-corner live pill.
// 可观测层重构方案 §7.4/§7.5: the data source is now /monitor/health (监控层裁决),
// not the raw /health/deps liveness — so a layer with an active alert renders
// down even while its /health returns 200. Gated on a token so the public error
// pages (404/500/403) that also render <StatusBar/> don't fire an unauthenticated
// request (which the api interceptor would bounce to /login).
import { useQuery } from '@tanstack/react-query';
import { endpoints, tokenStore } from './api';

export function useSystemHealth() {
  return useQuery({
    queryKey: ['monitor-health'],
    queryFn: endpoints.monitorHealth,
    enabled: !!tokenStore.get(),
    refetchInterval: 30_000,
    staleTime: 20_000,
    retry: 1,
  });
}
