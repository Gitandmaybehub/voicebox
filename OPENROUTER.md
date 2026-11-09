# Voicebox OpenRouter

![OpenRouter settings](docs/openrouter-settings.png)

This fork adds cloud voice cloning to Voicebox's existing profiles, generation history, downloads, retries and queue. The upstream README and MIT license are preserved.

## Use it

1. Open **Voicebox OpenRouter**.
2. Open **Settings → OpenRouter**, paste your key, click **Save**, then **Check**.
3. Leave the model on **HeyGen Voice**, or choose another available model.
4. Create a cloned voice profile. Upload or record a clear 2–30 second sample of your own voice. Reference text is optional for OpenRouter. If supplied, it must match the recording exactly.
5. Choose **OpenRouter** in Generate, select the profile, enter your script and generate. Download the completed audio from history.

The fork checks the provider's current voice-cloning support before generating. It does not silently substitute a stock voice or a different model. OpenRouter usage is billed to your key. Audio and scripts are sent to OpenRouter and its selected provider only when you generate.

## Models

Checked November 8, 2026:

| Model | Purpose |
|---|---|
| `heygen/heygen-voice-1` | Default quality choice |
| `heygen/heygen-voice-1-turbo` | Faster HeyGen option |
| `fish-audio/drama-3` | Alternative provider |

HeyGen Voice led Artificial Analysis's controlled English cloned-voice quality table when checked. This is a benchmark result, not a guarantee that it will sound best for every speaker. Compare a short sample of your own voice before generating a full lesson series. Popularity and voice similarity are different measures.

Sources: [OpenRouter voice cloning](https://openrouter.ai/docs/guides/overview/multimodal/tts), [speech API](https://openrouter.ai/docs/api/api-reference/tts/create-speech), [HeyGen provider capabilities](https://openrouter.ai/api/v1/models/heygen/heygen-voice-1/endpoints), and [Artificial Analysis controlled voices](https://artificialanalysis.ai/text-to-speech/leaderboard/controlled-voice).

## Private data

The API key is saved on the server in a user-only file outside this repository. The UI never receives the saved key. On macOS, the key file is under `~/Library/Application Support/voicebox-private/`; recordings and history for this app are under `~/Library/Application Support/com.gng.voicebox/`.

The native app listens on this Mac by default. Do not expose the server publicly without adding authenticated access. The fork has its own app identity and does not install upstream app updates automatically. It uses the same local server port as upstream, so run one Voicebox server at a time.

## Build and checks

Use upstream's Python 3.12 setup and Bun dependencies. Then build with `bun run build`. The OpenRouter integration needs no local voice model download.

Focused checks:

```sh
python -m pytest backend/tests/test_openrouter.py backend/tests/test_browser_write_origin.py backend/tests/test_generation_engine_resolution.py backend/tests/test_profile_duplicate_names.py backend/tests/test_profile_delete_cascade.py backend/tests/test_generation_download.py backend/tests/test_task_queue_cancellation.py -q
bun run typecheck
bun run build:web
```

Provider response tests use simulated audio and fake credentials. They check decoding, saved audio, retry behavior and error handling. They do not measure real voice similarity. No real voice generation has been verified without an actual key and a confirmed voice recording.

Verified in this fork: 106 focused backend tests passed, TypeScript checks passed, and the production web build passed. Browser checks covered saving and removing a test key, a real rejected-key response from OpenRouter, model persistence after reloading, creating a sample without a transcript or local download, and selecting a replacement after deleting a profile. Temporary test profiles and the test key were removed. [Watch the setup preview](docs/openrouter-walkthrough.mp4).
