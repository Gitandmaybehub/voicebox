import { Link } from '@tanstack/react-router';
import type { UseFormReturn } from 'react-hook-form';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import type { VoiceProfileResponse } from '@/lib/api/types';
import { DEFAULT_OPENROUTER_MODEL } from '@/lib/api/openrouter';
import type { GenerationFormValues } from '@/lib/hooks/useGenerationForm';
import { useOpenRouter } from '@/lib/hooks/useOpenRouter';

export function OpenRouterControls({
  form,
  profile,
}: {
  form: UseFormReturn<GenerationFormValues>;
  profile?: VoiceProfileResponse | null;
}) {
  const { settings, models, verification } = useOpenRouter();
  const value = form.watch('openrouterModel') || settings.data?.model || DEFAULT_OPENROUTER_MODEL;
  const model = models.data?.models.find((m) => m.id === value);
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
        onValueChange={(v) => form.setValue('openrouterModel', v, { shouldDirty: true })}
      >
        <SelectTrigger className="h-8 text-xs" aria-label="OpenRouter voice model">
          <SelectValue />
        </SelectTrigger>
        <SelectContent side="top">
          {!model && <SelectItem value={value}>{value} (unverified)</SelectItem>}
          {models.data?.models.map((m) => (
            <SelectItem
              key={m.id}
              value={m.id}
              disabled={!m.available || !m.supports_voice_cloning}
            >
              {m.name}
              {m.id === models.data.recommended_model ? ' · Recommended' : ''}
              {!m.available ? ' · Unavailable' : !m.supports_voice_cloning ? ' · No cloning' : ''}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <p role="status" className="text-xs text-muted-foreground">
        {status}{' '}
        <Link to="/settings/openrouter" className="underline">
          OpenRouter settings
        </Link>
      </p>
      <p className="text-xs text-muted-foreground">
        Audio and scripts go to OpenRouter when you generate
      </p>
    </div>
  );
}
