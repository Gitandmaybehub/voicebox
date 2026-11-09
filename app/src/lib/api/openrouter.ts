import { useServerStore } from '@/stores/serverStore';

export const DEFAULT_OPENROUTER_MODEL = 'heygen/heygen-voice-1-turbo:free';
export interface OpenRouterSettings {
  configured: boolean;
  model: string;
}
export interface OpenRouterModel {
  id: string;
  name: string;
  description: string;
  available: boolean;
  supports_voice_cloning: boolean;
  rank: number | null;
  weekly_requests: number | null;
  pricing?: Record<string, unknown>;
  error?: string;
}
export interface OpenRouterModels {
  models: OpenRouterModel[];
  recommended_model: string;
  selected_model: OpenRouterModel | null;
  source_url: string;
  fetched_at: string | null;
  data_date: string | null;
  stale: boolean;
  refresh_interval_seconds: number;
  error?: string;
  // Session-only rows preserve a pending selection when rankings change.
  previous_models?: OpenRouterModel[];
}

export function formatOpenRouterModelName(model: Pick<OpenRouterModel, 'id' | 'name'>): string {
  const name = model.name.replace(/\s*\((Free|Paid)\)$/i, '');
  return `${name} (${model.id.endsWith(':free') ? 'Free' : 'Paid'})`;
}

export function findOpenRouterModel(catalog: OpenRouterModels | undefined, id: string) {
  return (
    catalog?.models.find((model) => model.id === id) ??
    (catalog?.selected_model?.id === id ? catalog.selected_model : undefined) ??
    catalog?.previous_models?.find((model) => model.id === id)
  );
}

export function formatOpenRouterOption(model: OpenRouterModel): string {
  return `${model.rank == null ? '' : `#${model.rank} · `}${formatOpenRouterModelName(model)}${
    !model.supports_voice_cloning ? ' · No voice cloning' : ''
  }${!model.available ? ' · Unavailable' : ''}`;
}

// Use the backend snapshot's age, not the time it arrived in this browser.
export function getOpenRouterRefreshAt(
  catalog: OpenRouterModels | undefined,
  receivedAt: number,
  failedAt = 0,
): number {
  const retryDelay = 5 * 60 * 1000;
  if (failedAt > receivedAt) return failedAt + retryDelay;
  if (!catalog) return 0;
  const ttl =
    catalog.refresh_interval_seconds > 0 && Number.isFinite(catalog.refresh_interval_seconds)
      ? catalog.refresh_interval_seconds * 1000
      : 60 * 60 * 1000;
  const fetchedAt = catalog.fetched_at ? Date.parse(catalog.fetched_at) : NaN;
  const expiresAt = fetchedAt + ttl;
  // An expired/error response must not cause an immediate refetch loop.
  if (catalog.stale || catalog.error || !Number.isFinite(expiresAt) || expiresAt <= receivedAt) {
    return receivedAt + retryDelay;
  }
  return expiresAt;
}

export function formatOpenRouterUpdated(catalog?: OpenRouterModels): string {
  const date = catalog?.fetched_at ? new Date(catalog.fetched_at) : null;
  const now = new Date();
  const day =
    date && date.toDateString() !== now.toDateString()
      ? `${date.toLocaleDateString([], { month: 'short', day: 'numeric', ...(date.getFullYear() !== now.getFullYear() ? { year: 'numeric' as const } : {}) })}, `
      : '';
  const updated =
    date && !Number.isNaN(date.getTime())
      ? `Updated ${day}${date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', hour12: true })}`
      : 'Not updated yet';
  return `${updated} · Refreshes hourly`;
}

// Never echo provider response bodies: validation errors can include submitted secrets.
// Keys go directly to the server, never through query mutation caches or browser storage.
async function request<T>(path: string, method = 'GET', body?: object): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${useServerStore.getState().serverUrl}${path}`, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new Error('Cannot reach the Voicebox server. Check the server connection.');
  }
  if (!response.ok) {
    throw new Error(
      response.status === 401 || response.status === 403
        ? 'OpenRouter rejected this key. Check its access and try again.'
        : `OpenRouter request failed (${response.status}). Check the server and try again.`,
    );
  }
  return response.json();
}
export const openRouterApi = {
  settings: () => request<OpenRouterSettings>('/settings/openrouter'),
  models: (force = false) =>
    request<OpenRouterModels>(`/openrouter/models${force ? '?refresh=true' : ''}`),
  save: (patch: { api_key?: string; model?: string; clear_key?: boolean }) =>
    request<OpenRouterSettings>('/settings/openrouter', 'PUT', patch),
  check: () => request<{ status: string }>('/settings/openrouter/test', 'POST', {}),
};
