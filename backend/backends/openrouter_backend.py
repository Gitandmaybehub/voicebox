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

    async def create_voice_prompt(self, audio_path, reference_text, use_cache=True):
        try:
            # Bounded decode, mono PCM16 WAV, at most 3 minutes and 15 MiB.
            with sf.SoundFile(audio_path) as source:
                sr = source.samplerate
                total_frames = source.frames
                if not 8000 <= sr <= 192000 or not 1 <= source.channels <= 8:
                    raise ValueError()
                frames = min(int(sr * 180), (15 * 1024 * 1024 - 4096) // 2)
                audio = source.read(frames, dtype="float32", always_2d=True).mean(axis=1)
            if not len(audio) or not np.isfinite(audio).all() or np.max(np.abs(audio)) < 1e-6:
                raise ValueError()
            buf = io.BytesIO()
            sf.write(buf, audio, sr, format="WAV", subtype="PCM_16")
            references = [{"type": "input_audio", "input_audio": {"data": "data:audio/wav;base64," + base64.b64encode(buf.getvalue()).decode("ascii")}}]
            # A cropped clip must not carry the full original transcript.
            if reference_text and len(reference_text) <= 10000 and total_frames <= frames:
                references.append({"type": "text", "text": reference_text})
            return {"input_references": references}, False
        except Exception:
            raise api.OpenRouterError("Cannot read the profile recording. Add a clear WAV recording and try again.") from None

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
