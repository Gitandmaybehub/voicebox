import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  DEFAULT_OPENROUTER_MODEL,
  findOpenRouterModel,
  formatOpenRouterOption,
  formatOpenRouterUpdated,
  openRouterApi,
} from '@/lib/api/openrouter';
import { useOpenRouter } from '@/lib/hooks/useOpenRouter';
import { useServerStore } from '@/stores/serverStore';
import { SettingRow, SettingSection } from './SettingRow';

export function OpenRouterPage() {
  const { settings, models, verification, refreshModels, refetchModelsIfStale } = useOpenRouter();
  const queryClient = useQueryClient();
  const server = useServerStore((state) => state.serverUrl);
  const [key, setKey] = useState('');
  const [model, setModel] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const selected = model ?? settings.data?.model ?? DEFAULT_OPENROUTER_MODEL;
  const selectedModel = findOpenRouterModel(models.data, selected);
  const currentSelections = [
    ...new Set([selected, settings.data?.model].filter((id): id is string => !!id)),
  ].filter((id) => !models.data?.models.some((row) => row.id === id));
  const catalogError = models.error?.message || models.data?.error;
  const available = selectedModel?.available && selectedModel.supports_voice_cloning;

  async function act(action: 'save' | 'check' | 'remove') {
    setBusy(true);
    setMessage('');
    try {
      if (action === 'check') {
        const { data: result } = await verification.refetch({ throwOnError: true });
        setMessage(
          ['ok', 'success', 'valid', 'connected'].includes(result?.status ?? '')
            ? 'Key verified.'
            : 'Key check did not confirm access. Check the key and try again.',
        );
      } else {
        const saved = await openRouterApi.save(
          action === 'remove'
            ? { clear_key: true }
            : { ...(key.trim() ? { api_key: key.trim() } : {}), model: selected },
        );
        await queryClient.resetQueries({
          queryKey: ['openrouter'],
          predicate: (q) => q.queryKey[2] === 'verification',
        });
        queryClient.setQueryData(['openrouter', server, 'settings'], saved);
        setKey('');
        setModel(null);
        setMessage(action === 'remove' ? 'Key removed.' : 'Saved.');
      }
      await queryClient.invalidateQueries({ queryKey: ['openrouter'] });
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'OpenRouter request failed.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="max-w-2xl space-y-8">
      <SettingSection title="OpenRouter" description="Cloud voice cloning">
        <SettingRow
          title="API key"
          htmlFor="openrouter-key"
          description={
            settings.isError
              ? 'Could not read key status.'
              : settings.isPending
                ? 'Checking key…'
                : settings.data?.configured
                  ? 'Key saved'
                  : 'No key saved'
          }
        >
          <Input
            id="openrouter-key"
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder="OpenRouter API key"
            disabled={busy}
          />
          <p className="text-xs text-muted-foreground mt-2">
            Audio and scripts go to OpenRouter when you generate
          </p>
        </SettingRow>
        <SettingRow title="Voice model">
          <Select
            value={selected}
            onValueChange={setModel}
            onOpenChange={refetchModelsIfStale}
            disabled={busy}
          >
            <SelectTrigger aria-label="OpenRouter default voice model">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {currentSelections.length > 0 && (
                <SelectGroup>
                  <SelectLabel>Current selection</SelectLabel>
                  {currentSelections.map((id) => {
                    const current = findOpenRouterModel(models.data, id);
                    return (
                      <SelectItem
                        key={id}
                        value={id}
                        disabled={!current?.available || !current.supports_voice_cloning}
                      >
                        {current
                          ? formatOpenRouterOption({ ...current, rank: null })
                          : `${id} (unverified)`}
                      </SelectItem>
                    );
                  })}
                </SelectGroup>
              )}
              <SelectGroup>
                <SelectLabel>Top 15 this week</SelectLabel>
                {models.data?.models.map((m) => (
                  <SelectItem
                    key={m.id}
                    value={m.id}
                    disabled={!m.available || !m.supports_voice_cloning}
                  >
                    {formatOpenRouterOption(m)}
                    {m.id === models.data.recommended_model ? ' · Recommended' : ''}
                  </SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
          <p className="text-xs text-muted-foreground mt-2">
            {formatOpenRouterUpdated(models.data)}
            {' · '}
            <a
              href={models.data?.source_url || 'https://openrouter.ai/rankings/speech?view=week'}
              target="_blank"
              rel="noreferrer"
              className="underline"
            >
              OpenRouter
            </a>
            {' ('}
            <a
              href="https://creativecommons.org/licenses/by/4.0/"
              target="_blank"
              rel="noreferrer"
              className="underline"
            >
              CC BY 4.0
            </a>
            {')'}
          </p>
          {(catalogError || models.data?.stale) && (
            <output className="block text-xs text-muted-foreground">
              {models.data?.stale || (models.isError && models.data)
                ? 'Showing saved rankings. '
                : ''}
              {catalogError || 'Rankings could not be updated. Try Refresh.'}
            </output>
          )}
          <p className="text-sm text-muted-foreground mt-2">
            {models.isError
              ? 'Could not check models. Refresh to retry.'
              : models.isPending
                ? 'Checking models…'
                : available
                  ? 'Voice cloning available'
                  : 'Voice cloning unavailable or unverified.'}
          </p>
        </SettingRow>
        <div className="flex flex-wrap gap-2 pt-4">
          <Button
            disabled={busy || settings.isPending || (!key.trim() && !settings.data?.configured)}
            onClick={() => void act('save')}
          >
            Save
          </Button>
          <Button
            variant="outline"
            disabled={busy || !settings.data?.configured || !!key.trim()}
            onClick={() => void act('check')}
          >
            Check
          </Button>
          <Button
            variant="outline"
            disabled={busy || !settings.data?.configured}
            onClick={() => void act('remove')}
          >
            Remove
          </Button>
          <Button
            variant="ghost"
            disabled={busy || models.isFetching}
            onClick={() => {
              void settings.refetch();
              void refreshModels().catch(() => {
                /* Query error is displayed above. */
              });
            }}
          >
            {models.isFetching ? 'Refreshing…' : 'Refresh'}
          </Button>
        </div>
        <output className="block pt-3 text-sm">{busy ? 'Working…' : message}</output>
      </SettingSection>
    </div>
  );
}
