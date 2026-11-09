import { useQuery } from '@tanstack/react-query';
import { openRouterApi } from '@/lib/api/openrouter';
import { useServerStore } from '@/stores/serverStore';

export function useOpenRouter() {
  const server = useServerStore((s) => s.serverUrl);
  const settings = useQuery({
    queryKey: ['openrouter', server, 'settings'],
    queryFn: openRouterApi.settings,
    retry: false,
  });
  const models = useQuery({
    queryKey: ['openrouter', server, 'models'],
    queryFn: openRouterApi.models,
    retry: false,
  });
  const verification = useQuery({
    queryKey: ['openrouter', server, 'verification'],
    queryFn: openRouterApi.check,
    enabled: false,
    retry: false,
    staleTime: Infinity,
  });
  return { settings, models, verification };
}
