import { useQuery, useQueryClient } from '@tanstack/react-query';
import { getOpenRouterRefreshAt, type OpenRouterModels, openRouterApi } from '@/lib/api/openrouter';
import { useServerStore } from '@/stores/serverStore';

export function useOpenRouter() {
  const queryClient = useQueryClient();
  const server = useServerStore((s) => s.serverUrl);
  const settings = useQuery({
    queryKey: ['openrouter', server, 'settings'],
    queryFn: openRouterApi.settings,
    retry: false,
  });
  const modelsKey = ['openrouter', server, 'models'];
  async function fetchModels(force = false): Promise<OpenRouterModels> {
    const next = await openRouterApi.models(force);
    const previous = queryClient.getQueryData<OpenRouterModels>(modelsKey);
    const remembered = new Map(
      [
        ...(previous?.previous_models ?? []),
        ...(previous?.models ?? []),
        ...(previous?.selected_model ? [previous.selected_model] : []),
      ].map((model) => [model.id, model]),
    );
    // Fresh rows always win, including changes to availability and clone support.
    for (const model of next.models) remembered.delete(model.id);
    if (next.selected_model) remembered.delete(next.selected_model.id);
    return { ...next, previous_models: [...remembered.values()] };
  }
  function refreshAt(state: {
    data?: OpenRouterModels;
    dataUpdatedAt: number;
    errorUpdatedAt: number;
  }) {
    return getOpenRouterRefreshAt(state.data, state.dataUpdatedAt, state.errorUpdatedAt);
  }
  const models = useQuery({
    queryKey: modelsKey,
    queryFn: () => fetchModels(),
    retry: false,
    staleTime: (query) => Math.max(0, refreshAt(query.state) - query.state.dataUpdatedAt),
    refetchInterval: (query) => Math.max(1000, refreshAt(query.state) - Date.now()),
    refetchIntervalInBackground: false,
    refetchOnMount: (query) => (Date.now() >= refreshAt(query.state) ? 'always' : false),
    refetchOnWindowFocus: (query) => (Date.now() >= refreshAt(query.state) ? 'always' : false),
  });
  async function refreshModels() {
    await queryClient.cancelQueries({ queryKey: modelsKey, exact: true });
    return queryClient.fetchQuery({
      queryKey: modelsKey,
      queryFn: () => fetchModels(true),
      staleTime: 0,
      retry: false,
    });
  }
  function refetchModelsIfStale(open: boolean) {
    const state = queryClient.getQueryState<OpenRouterModels>(modelsKey);
    if (open && state && Date.now() >= refreshAt(state) && !models.isFetching) {
      void models.refetch();
    }
  }
  const verification = useQuery({
    queryKey: ['openrouter', server, 'verification'],
    queryFn: openRouterApi.check,
    enabled: false,
    retry: false,
    staleTime: Infinity,
  });
  return { settings, models, verification, refreshModels, refetchModelsIfStale };
}
