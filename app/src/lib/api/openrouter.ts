import { useServerStore } from '@/stores/serverStore';

export const DEFAULT_OPENROUTER_MODEL = 'heygen/heygen-voice-1';
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
}
export interface OpenRouterModels {
  models: OpenRouterModel[];
  recommended_model: string;
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
  models: () => request<OpenRouterModels>('/openrouter/models'),
  save: (patch: { api_key?: string; model?: string; clear_key?: boolean }) =>
    request<OpenRouterSettings>('/settings/openrouter', 'PUT', patch),
  check: () => request<{ status: string }>('/settings/openrouter/test', 'POST', {}),
};
