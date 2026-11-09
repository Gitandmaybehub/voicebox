import { zodResolver } from '@hookform/resolvers/zod';
import { useEffect, useState } from 'react';
import { useForm } from 'react-hook-form';
import * as z from 'zod';
import { useToast } from '@/components/ui/use-toast';
import { apiClient } from '@/lib/api/client';
import { DEFAULT_OPENROUTER_MODEL, findOpenRouterModel, openRouterApi } from '@/lib/api/openrouter';
import type { EffectConfig } from '@/lib/api/types';
import { engineSupportsInstruct } from '@/lib/constants/engines';
import { LANGUAGE_CODES, type LanguageCode } from '@/lib/constants/languages';
import { useGeneration } from '@/lib/hooks/useGeneration';
import { useModelDownloadToast } from '@/lib/hooks/useModelDownloadToast';
import { useOpenRouter } from '@/lib/hooks/useOpenRouter';
import { useGenerationSettings } from '@/lib/hooks/useSettings';
import { useGenerationStore } from '@/stores/generationStore';
import { useUIStore } from '@/stores/uiStore';

const generationSchema = z.object({
  text: z.string().min(1, '').max(50000),
  language: z.enum(LANGUAGE_CODES as [LanguageCode, ...LanguageCode[]]),
  seed: z.number().int().optional(),
  modelSize: z.enum(['1.7B', '0.6B', '1B', '3B']).optional(),
  instruct: z.string().max(500).optional(),
  engine: z
    .enum([
      'openrouter',
      'qwen',
      'qwen_custom_voice',
      'qwen_voice_design',
      'luxtts',
      'chatterbox',
      'chatterbox_turbo',
      'tada',
      'kokoro',
      'omnivoice',
    ])
    .optional(),
  personality: z.boolean().optional(),
  openrouterModel: z.string().optional(),
});

export type GenerationFormValues = z.infer<typeof generationSchema>;

interface UseGenerationFormOptions {
  onSuccess?: (generationId: string) => void;
  defaultValues?: Partial<GenerationFormValues>;
  getEffectsChain?: () => EffectConfig[] | undefined;
}

export function useGenerationForm(options: UseGenerationFormOptions = {}) {
  const { toast } = useToast();
  const generation = useGeneration();
  const cloud = useOpenRouter();
  const addPendingGeneration = useGenerationStore((state) => state.addPendingGeneration);
  const { settings: genSettings } = useGenerationSettings();
  const maxChunkChars = genSettings?.max_chunk_chars ?? 800;
  const crossfadeMs = genSettings?.crossfade_ms ?? 50;
  const normalizeAudio = genSettings?.normalize_audio ?? true;
  const selectedEngine = useUIStore((state) => state.selectedEngine);
  const [downloadingModelName, setDownloadingModelName] = useState<string | null>(null);
  const [downloadingDisplayName, setDownloadingDisplayName] = useState<string | null>(null);

  useModelDownloadToast({
    modelName: downloadingModelName || '',
    displayName: downloadingDisplayName || '',
    enabled: !!downloadingModelName,
  });

  const form = useForm<GenerationFormValues>({
    resolver: zodResolver(generationSchema),
    defaultValues: {
      text: '',
      language: 'en',
      seed: undefined,
      modelSize: '1.7B',
      instruct: '',
      engine: (selectedEngine as GenerationFormValues['engine']) || 'openrouter',
      personality: false,
      ...options.defaultValues,
    },
  });

  useEffect(() => {
    if (!form.getFieldState('openrouterModel').isDirty && cloud.settings.data) {
      form.setValue('openrouterModel', cloud.settings.data.model || DEFAULT_OPENROUTER_MODEL);
    }
  }, [cloud.settings.data, form]);

  async function handleSubmit(
    data: GenerationFormValues,
    selectedProfileId: string | null,
  ): Promise<void> {
    if (!selectedProfileId) {
      toast({
        title: 'No profile selected',
        description: 'Please select a voice profile from the cards above.',
        variant: 'destructive',
      });
      return;
    }

    try {
      const engine = data.engine || 'openrouter';
      let cloudModel: string | undefined;
      if (engine === 'openrouter') {
        const [settings, catalog, profile] = await Promise.all([
          openRouterApi.settings(),
          openRouterApi.models(),
          apiClient.getProfile(selectedProfileId),
        ]);
        if (!settings.configured)
          throw new Error('Add your OpenRouter key in Settings → OpenRouter.');
        if ((profile.voice_type || 'cloned') !== 'cloned' || profile.sample_count < 1) {
          throw new Error(
            'OpenRouter needs a cloned voice profile with an uploaded or recorded sample. Preset and designed voices are not supported.',
          );
        }
        cloudModel = data.openrouterModel || settings.model || DEFAULT_OPENROUTER_MODEL;
        const model =
          findOpenRouterModel(catalog, cloudModel) ??
          findOpenRouterModel(cloud.models.data, cloudModel);
        if (!model?.available || !model.supports_voice_cloning) {
          throw new Error(
            'This OpenRouter model is unavailable or voice cloning is unverified. Choose an available clone model.',
          );
        }
      }
      const modelName =
        engine === 'luxtts'
          ? 'luxtts'
          : engine === 'chatterbox'
            ? 'chatterbox-tts'
            : engine === 'chatterbox_turbo'
              ? 'chatterbox-turbo'
              : engine === 'tada'
                ? data.modelSize === '3B'
                  ? 'tada-3b-ml'
                  : 'tada-1b'
                : engine === 'kokoro'
                  ? 'kokoro'
                  : engine === 'qwen_voice_design'
                    ? 'qwen-voice-design-1.7B'
                    : engine === 'omnivoice'
                      ? 'omnivoice'
                      : engine === 'qwen_custom_voice'
                        ? `qwen-custom-voice-${data.modelSize}`
                        : `qwen-tts-${data.modelSize}`;
      const displayName =
        engine === 'luxtts'
          ? 'LuxTTS'
          : engine === 'chatterbox'
            ? 'Chatterbox TTS'
            : engine === 'chatterbox_turbo'
              ? 'Chatterbox Turbo'
              : engine === 'tada'
                ? data.modelSize === '3B'
                  ? 'TADA 3B Multilingual'
                  : 'TADA 1B'
                : engine === 'kokoro'
                  ? 'Kokoro 82M'
                  : engine === 'qwen_voice_design'
                    ? 'Qwen VoiceDesign 1.7B'
                    : engine === 'omnivoice'
                      ? 'OmniVoice'
                      : engine === 'qwen_custom_voice'
                        ? data.modelSize === '1.7B'
                          ? 'Qwen CustomVoice 1.7B'
                          : 'Qwen CustomVoice 0.6B'
                        : data.modelSize === '1.7B'
                          ? 'Qwen TTS 1.7B'
                          : 'Qwen TTS 0.6B';

      // Cloud engines never trigger local model discovery or downloads.
      if (engine !== 'openrouter') {
        const profile = await apiClient.getProfile(selectedProfileId);
        if ((profile.voice_type || 'cloned') === 'cloned') {
          const samples = await apiClient.listProfileSamples(selectedProfileId);
          if (!samples.length || samples.some((sample) => !sample.reference_text?.trim())) {
            throw new Error(
              'This local engine needs an exact transcript for each voice sample. Add the spoken words to the profile samples or use OpenRouter.',
            );
          }
        }
        try {
          const modelStatus = await apiClient.getModelStatus();
          const model = modelStatus.models.find((m) => m.model_name === modelName);

          if (model && !model.downloaded) {
            setDownloadingModelName(modelName);
            setDownloadingDisplayName(displayName);
          }
        } catch (error) {
          console.error('Failed to check model status:', error);
        }
      }

      const hasModelSizes =
        engine === 'qwen' || engine === 'qwen_custom_voice' || engine === 'tada';
      const supportsInstruct = engineSupportsInstruct(engine);
      const effectsChain = options.getEffectsChain?.();
      // This now returns immediately with status="generating"
      const result = await generation.mutateAsync({
        profile_id: selectedProfileId,
        text: data.text,
        language: data.language,
        seed: data.seed,
        model_size: hasModelSizes ? data.modelSize : undefined,
        engine,
        openrouter_model: cloudModel,
        instruct: supportsInstruct ? data.instruct || undefined : undefined,
        personality: data.personality || undefined,
        max_chunk_chars: maxChunkChars,
        crossfade_ms: crossfadeMs,
        normalize: normalizeAudio,
        effects_chain: effectsChain?.length ? effectsChain : undefined,
      });

      // Track this generation for SSE status updates
      addPendingGeneration(result.id);

      // Reset form immediately — user can start typing again
      form.reset({
        text: '',
        language: data.language,
        seed: undefined,
        modelSize: data.modelSize,
        instruct: '',
        engine: data.engine,
        openrouterModel: cloudModel ?? data.openrouterModel,
        personality: data.personality,
      });
      options.onSuccess?.(result.id);
    } catch (error) {
      toast({
        title: 'Generation failed',
        description: error instanceof Error ? error.message : 'Failed to generate audio',
        variant: 'destructive',
      });
    } finally {
      setDownloadingModelName(null);
      setDownloadingDisplayName(null);
    }
  }

  const selectedCloudModel =
    form.watch('openrouterModel') || cloud.settings.data?.model || DEFAULT_OPENROUTER_MODEL;
  const cloudModel = findOpenRouterModel(cloud.models.data, selectedCloudModel);
  const cloudUnavailable =
    form.watch('engine') === 'openrouter' &&
    (!cloud.settings.data?.configured ||
      cloud.settings.isError ||
      !cloudModel?.available ||
      !cloudModel.supports_voice_cloning);

  return {
    cloudUnavailable,
    form,
    handleSubmit,
    isPending: generation.isPending,
  };
}
