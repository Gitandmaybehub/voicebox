"""Cloud TTS backend using the same profile and generation pipeline as local TTS."""
import base64
from contextvars import ContextVar
import io

import numpy as np
import soundfile as sf

from ..services import openrouter as api


class OpenRouterBackend:
    device = "cpu"
    _model = ContextVar("openrouter_model", default=api.DEFAULT_MODEL)

    def is_loaded(self):
        return True  # No weights to download or load.

    def unload_model(self):
        pass

    async def load_model(self, model_size=None):
        self._model.set(api.resolve_model(model_size))

    REFERENCE_RATE = 24000
    MAX_REFERENCE_SECONDS = 180
    GAP_SECONDS = 0.2

    def _read_reference(self, audio_path):
        """Decode the complete reference and resample without changing pitch/speed."""
        from math import gcd
        from scipy.signal import resample_poly

        try:
            with sf.SoundFile(audio_path) as source:
                sr = source.samplerate
                if not 8000 <= sr <= 192000 or not 1 <= source.channels <= 8:
                    raise ValueError()
                if source.frames > sr * self.MAX_REFERENCE_SECONDS:
                    raise api.OpenRouterError("Combined reference exceeds 180 seconds including gaps. Shorten or remove samples; no audio was clipped.")
                audio = source.read(dtype="float32", always_2d=True).mean(axis=1)
            if not len(audio) or not np.isfinite(audio).all() or np.max(np.abs(audio)) < 1e-6:
                raise ValueError()
            if sr != self.REFERENCE_RATE:
                divisor = gcd(sr, self.REFERENCE_RATE)
                audio = resample_poly(audio, self.REFERENCE_RATE // divisor, sr // divisor)
            return audio
        except api.OpenRouterError:
            raise
        except Exception:
            raise api.OpenRouterError("Cannot read the profile recording. Add a clear WAV recording and try again.") from None

    async def combine_voice_prompts(self, audio_paths, reference_texts):
        import asyncio

        if not audio_paths or len(audio_paths) != len(reference_texts):
            raise api.OpenRouterError("Each profile recording must have its own transcript field.")
        parts = []
        frames = 0
        gap = np.zeros(round(self.GAP_SECONDS * self.REFERENCE_RATE), dtype=np.float32)
        for path in audio_paths:
            audio = await asyncio.to_thread(self._read_reference, path)
            frames += len(audio) + (len(gap) if parts else 0)
            if frames > self.MAX_REFERENCE_SECONDS * self.REFERENCE_RATE:
                raise api.OpenRouterError("Combined reference exceeds 180 seconds including gaps. Shorten or remove samples; no audio was clipped.")
            if parts:
                parts.append(gap)
            parts.append(audio)
        # Partial transcripts do not describe the complete recording.
        text = " ".join(t.strip() for t in reference_texts) if all(t and t.strip() for t in reference_texts) else ""
        return np.concatenate(parts), text

    async def create_voice_prompt(self, audio_path, reference_text, use_cache=True):
        import asyncio

        audio = await asyncio.to_thread(self._read_reference, audio_path)
        buf = io.BytesIO()
        sf.write(buf, audio, self.REFERENCE_RATE, format="WAV", subtype="PCM_16")
        references = [{"type": "input_audio", "input_audio": {"data": "data:audio/wav;base64," + base64.b64encode(buf.getvalue()).decode("ascii")}}]
        if reference_text and reference_text.strip() and len(reference_text) <= 10000:
            references.append({"type": "text", "text": reference_text})
        return {"input_references": references}, False

    async def generate(self, text, voice_prompt, language="en", seed=None, instruct=None):
        model = self._model.get()
        await api.require_cloning(model)
        references = voice_prompt.get("input_references")
        if not references or not any(p.get("type") == "input_audio" for p in references):
            raise api.OpenRouterError("A profile recording is required for OpenRouter voice cloning.")
        heygen = model.startswith("heygen/")
        payload = {"model": model, "input": text, "response_format": "pcm" if heygen else "mp3", "input_references": references}
        if instruct and not heygen:
            payload["instructions"] = instruct
        raw, mime = await api.request("/audio/speech", authenticated=True, payload=payload)
        try:
            if heygen:
                if mime != "audio/pcm" or not raw or len(raw) % 2:
                    raise ValueError()
                return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0, 44100
            if mime != "audio/mpeg":
                raise ValueError()
            audio, sr = sf.read(io.BytesIO(raw), dtype="float32", always_2d=True)
            audio = audio.mean(axis=1)
            if not len(audio) or not np.isfinite(audio).all():
                raise ValueError()
            return audio, sr
        except Exception:
            raise api.OpenRouterError("OpenRouter returned invalid audio. No automatic retry was made.") from None
