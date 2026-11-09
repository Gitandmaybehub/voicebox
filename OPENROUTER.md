# Voicebox OpenRouter

![OpenRouter settings](docs/openrouter-settings.png)

This fork adds cloud voice cloning to Voicebox's existing profiles, generation history, downloads, retries and queue. The upstream README and MIT license are preserved.

## Use it

1. Open **Voicebox OpenRouter**.
2. Open **Settings → OpenRouter**, paste your key, click **Save**, then **Check**.
3. Leave the model on **HeyGen Voice Turbo (Free)**, or explicitly choose a paid model.
4. Create a cloned voice profile. Upload or record clear samples of your own voice. OpenRouter profiles allow up to **3 minutes total**, including short pauses between samples. Local voice engines keep their 30-second sample limit. Reference text is optional for OpenRouter. If supplied, it must match the recording exactly.
5. Choose **OpenRouter** in Generate, select the profile, enter your script and generate. Download the completed audio from history.

Every recording in the selected profile is combined into one reference file. The three-minute limit is checked before generation; an overlong profile gets an error without dropping recordings or clipping speech. A transcript is sent only when every sample has reference text.

The fork checks the provider's current voice-cloning support before generating. Free Turbo uses the exact `:free` model. If it is unavailable, generation stops. It never falls back to a paid model. Paid options charge your OpenRouter key. Audio and scripts are sent to OpenRouter and its selected provider only when you generate.

## Models

Checked November 8, 2026:

| Model | Purpose |
|---|---|
| `heygen/heygen-voice-1-turbo:free` | Free; default |
| `heygen/heygen-voice-1` | Paid quality option |
| `heygen/heygen-voice-1-turbo` | Paid Turbo option |
| `fish-audio/drama-3` | Paid alternative provider |

OpenRouter currently lists the free Turbo option as ending November 30, 2026. Availability and rate limits are controlled by the provider.

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

Provider response tests use simulated audio and fake credentials. They check decoding, saved audio, retry behavior and error handling. They do not measure real voice similarity.

The free-model and sample-handling update passed 132 focused backend tests, including three-sample ordering, pitch and duration preservation, partial transcripts, the combined limit, repeatable legacy-data migration, and refusal to fall back from free to paid. TypeScript and production builds passed. Browser checks recorded 32.1 seconds, accepted a 35-second additional cloud sample, and rejected that file for a local profile. [Watch the setup preview](docs/openrouter-walkthrough.mp4).

The updated Mac app was installed and checked. Free Turbo is selected in both Settings and Generate. A real request to the exact `:free` model completed successfully with all three existing reference samples. The assembled 71.592-second reference was checked against the original PCM audio, with all source frames preserved and only short gaps added between clips. Original recordings remained unchanged. The resulting voice preview stays in private app storage and is not included in this repository.

Earlier checks covered saving and removing a test key, a real rejected-key response from OpenRouter, model persistence, blank transcripts, sample counts, and selection after deleting a profile. The original Apple Silicon app was built, installed and opened. Temporary test profiles and the test key were removed. Its first sample took about 36 seconds to prepare.
