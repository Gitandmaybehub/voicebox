import { Link } from '@tanstack/react-router';
import type { UseFormReturn } from 'react-hook-form';
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
} from '@/lib/api/openrouter';
import type { VoiceProfileResponse } from '@/lib/api/types';
import type { GenerationFormValues } from '@/lib/hooks/useGenerationForm';
import { useOpenRouter } from '@/lib/hooks/useOpenRouter';

export function OpenRouterControls({
  form,
  profile,
}: {
  form: UseFormReturn<GenerationFormValues>;
  profile?: VoiceProfileResponse | null;
}) {
  const { settings, models, verification, refetchModelsIfStale } = useOpenRouter();
  const value = form.watch('openrouterModel') || settings.data?.model || DEFAULT_OPENROUTER_MODEL;
  const model = findOpenRouterModel(models.data, value);
  const currentSelections = [
    ...new Set([value, settings.data?.model].filter((id): id is string => !!id)),
  ].filter((id) => !models.data?.models.some((row) => row.id === id));
  const catalogError = models.error?.message || models.data?.error;
  const profileReady =
    profile && (profile.voice_type || 'cloned') === 'cloned' && profile.sample_count > 0;
  const status = !profileReady
    ? 'Select a cloned profile with a voice sample.'
    : settings.isError || models.isError
      ? 'Could not check OpenRouter. Refresh in Settings.'
      : settings.isPending || models.isPending
        ? 'Checking OpenRouter…'
        : !settings.data?.configured
          ? 'Add your OpenRouter key in Settings.'
          : !model?.available || !model.supports_voice_cloning
            ? 'This model is unavailable or clone support is unverified.'
            : verification.data?.status !== 'ok' || verification.isError
              ? 'Voice cloning available · Key not checked'
              : 'Voice cloning available · Key verified';
  return (
    <div className="mt-3 space-y-2 px-2">
      <Select
        value={value}
        onOpenChange={refetchModelsIfStale}
        onValueChange={(v) => form.setValue('openrouterModel', v, { shouldDirty: true })}
      >
        <SelectTrigger className="h-8 text-xs" aria-label="OpenRouter voice model">
          <SelectValue />
        </SelectTrigger>
        <SelectContent side="top">
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
          {models.data?.stale || (models.isError && models.data) ? 'Showing saved rankings. ' : ''}
          {catalogError || 'Rankings could not be updated. Try Refresh.'}
        </output>
      )}
      <output className="block text-xs text-muted-foreground">
        {status}{' '}
        <Link to="/settings/openrouter" className="underline">
          OpenRouter settings
        </Link>
      </output>
      <p className="text-xs text-muted-foreground">
        Audio and scripts go to OpenRouter when you generate
      </p>
    </div>
  );
}
