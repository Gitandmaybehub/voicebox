"""Offline transport, credential, audio, and shared pipeline coverage."""
import asyncio
import base64
import io
import json
import stat
from unittest.mock import AsyncMock

import httpx
import numpy as np
import pytest
import soundfile as sf
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.services import openrouter as api
from backend.backends.openrouter_backend import OpenRouterBackend
from backend.routes.openrouter import router


@pytest.fixture(autouse=True)
def private_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "settings_path", lambda: tmp_path / "private" / "openrouter.json")


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def transport(monkeypatch, handler):
    original = httpx.AsyncClient
    def client(**kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        return original(**kwargs, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(api.httpx, "AsyncClient", client)


def test_settings_redacted_private_partial_and_clear(client):
    secret = "sk-or-fake-test-only"
    assert client.get('/settings/openrouter').json() == {"configured": False, "model": api.DEFAULT_MODEL}
    result = client.put('/settings/openrouter', json={"api_key": secret})
    assert result.status_code == 200
    assert secret not in result.text
    assert stat.S_IMODE(api.settings_path().stat().st_mode) == 0o600
    result = client.put('/settings/openrouter', json={"model": "fish-audio/drama-3"})
    assert result.json() == {"configured": True, "model": "fish-audio/drama-3"}
    assert json.loads(api.settings_path().read_text())["api_key"] == secret
    assert client.put('/settings/openrouter', json={"clear_key": True}).json()["configured"] is False
    assert secret not in api.settings_path().read_text()


@pytest.mark.parametrize('body', [
    '{"api_key":"SECRET",',
    '{"api_key":{"SECRET":1}}',
    '{"api_key":"SECRET","clear_key":"bad"}',
    '{"api_key":"SECRET","model":[]}',
    '["SECRET"]',
])
def test_invalid_settings_never_echo_payload(client, body):
    result = client.put('/settings/openrouter', content=body)
    assert result.status_code == 400
    assert "SECRET" not in result.text


def test_key_check_is_free_and_redacted(client, monkeypatch):
    api.update_settings({"api_key": "fake-secret"})
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"data": {"limit_remaining": 999, "label": "fake-secret"}})
    transport(monkeypatch, handler)
    assert client.post('/settings/openrouter/test', json={}).json() == {"status": "ok"}
    assert len(seen) == 1
    assert str(seen[0].url) == api.BASE_URL + '/key'
    assert seen[0].method == 'GET'


@pytest.mark.asyncio
@pytest.mark.parametrize('code', [301, 400, 401, 402, 403, 404, 413, 429, 502])
async def test_provider_errors_no_echo_or_retries(monkeypatch, code):
    api.update_settings({"api_key": "fake-secret"})
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(code, text='fake-secret private voice base64', headers={"Location": "https://evil.example"})
    transport(monkeypatch, handler)
    with pytest.raises(api.OpenRouterError) as error:
        await api.request('/audio/speech', authenticated=True, payload={"input": "hi"})
    assert "fake-secret" not in str(error.value)
    assert 'base64' not in str(error.value)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_discovery_fails_closed_and_generation_requires_caps(monkeypatch):
    def handler(request):
        if 'turbo' in request.url.path:
            return httpx.Response(503)
        return httpx.Response(200, json={"data": {"endpoints": [{"status": 0, "supports_voice_cloning": False}]}})
    transport(monkeypatch, handler)
    result = await api.list_models()
    assert len(result['models']) == 4
    assert not any(m['supports_voice_cloning'] for m in result['models'])
    assert result['models'][0]['error']
    backend = OpenRouterBackend()
    await backend.load_model('heygen/heygen-voice-1')
    with pytest.raises(api.OpenRouterError, match='cannot clone'):
        await backend.generate('hi', {"input_references": [{"type": "input_audio"}]})


@pytest.mark.asyncio
async def test_pcm_reference_and_no_voice_override(monkeypatch, tmp_path):
    path = tmp_path / 'reference.wav'
    sf.write(path, np.full(24000, .25), 24000)
    backend = OpenRouterBackend()
    prompt, cached = await backend.create_voice_prompt(str(path), 'reference words')
    assert not cached
    decoded = base64.b64decode(prompt['input_references'][0]['input_audio']['data'].split(',')[1])
    assert sf.info(io.BytesIO(decoded)).subtype == 'PCM_16'
    api.update_settings({"api_key": "fake-secret"})
    seen = []
    def handler(request):
        if request.method == 'GET':
            return httpx.Response(200, json={"data": {"endpoints": [{"status": 0, "supports_voice_cloning": True}]}})
        seen.append(json.loads(request.content))
        return httpx.Response(200, content=np.array([-32768, 0, 32767], dtype='<i2').tobytes(), headers={"content-type": "audio/pcm"})
    transport(monkeypatch, handler)
    await backend.load_model(api.DEFAULT_MODEL)
    audio, sr = await backend.generate('new words', prompt)
    assert sr == 44100
    np.testing.assert_allclose(audio, [-1, 0, 32767 / 32768])
    assert seen[0]['input_references'] == prompt['input_references']
    assert 'voice' not in seen[0]
    assert seen[0]['model'] == api.DEFAULT_MODEL


@pytest.mark.asyncio
async def test_mp3_decode_and_context_isolation(monkeypatch):
    monkeypatch.setattr(api, 'require_cloning', AsyncMock())
    buf = io.BytesIO()
    sf.write(buf, np.sin(np.arange(4410) * .1), 44100, format='MP3')
    seen = []
    async def request(path, **kwargs):
        model = kwargs['payload']['model']
        seen.append(model)
        if model.startswith('fish'):
            return buf.getvalue(), 'audio/mpeg'
        return b'\x00\x01', 'audio/pcm'
    monkeypatch.setattr(api, 'request', request)
    backend = OpenRouterBackend()
    async def work(model):
        await backend.load_model(model)
        await asyncio.sleep(0)
        audio, sr = await backend.generate('hi', {'input_references': [{'type': 'input_audio'}]})
        assert len(audio) and sr == 44100
    await asyncio.gather(work(api.DEFAULT_MODEL), work('fish-audio/drama-3'))
    assert seen == [api.DEFAULT_MODEL, 'fish-audio/drama-3']


@pytest.mark.asyncio
async def test_timeout_and_cancellation_propagate(monkeypatch):
    async def timeout(request):
        raise httpx.ReadTimeout('fake-secret')
    transport(monkeypatch, timeout)
    with pytest.raises(api.OpenRouterError, match='timed out'):
        await api.request('/key')
    async def cancel(request):
        raise asyncio.CancelledError()
    # Swap transport without nesting our patched constructor.
    monkeypatch.undo()
    transport(monkeypatch, cancel)
    with pytest.raises(asyncio.CancelledError):
        await api.request('/key')


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from backend import config
    from backend.database import Base, VoiceProfile, ProfileSample
    from backend.services import generation
    from backend.routes import generations
    engine = create_engine('sqlite:///' + str(tmp_path / 'test.sqlite'))
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    db = factory()
    monkeypatch.setattr(config, '_data_dir', tmp_path)
    monkeypatch.setattr(generation, 'get_db', lambda: iter([factory()]))
    db.add(VoiceProfile(id='p', name='Cloud voice', voice_type='cloned', default_engine='openrouter', language='en'))
    audio = tmp_path / 'sample.wav'
    sf.write(audio, np.sin(np.arange(24000) * .05) * .2, 24000)
    db.add(ProfileSample(id='a', profile_id='p', audio_path=str(audio), reference_text=''))
    db.commit()
    queued = []
    monkeypatch.setattr(generations, 'enqueue_generation', lambda ident, work: queued.append(work))
    yield db, queued, audio
    for coroutine in queued:
        coroutine.close()
    db.close()
    engine.dispose()


@pytest.mark.asyncio
async def test_profile_detail_reports_sample_count_for_generation(pipeline):
    from backend.services import profiles

    db, _, _ = pipeline
    detail = await profiles.get_profile("p", db)
    listed = await profiles.list_profiles(db)
    assert detail.sample_count == 1
    assert detail.sample_count == listed[0].sample_count


@pytest.mark.asyncio
async def test_pipeline_persists_model_wav_and_stable_retry(pipeline, monkeypatch):
    from backend import models
    from backend.routes import generations
    from backend.database import Generation
    db, queue, _ = pipeline
    api.update_settings({'api_key': 'fake-secret', 'model': api.DEFAULT_MODEL})
    requests = []
    def handler(request):
        if request.method == 'GET':
            return httpx.Response(200, json={'data': {'endpoints': [{'status': 0, 'supports_voice_cloning': True}]}})
        requests.append(json.loads(request.content))
        return httpx.Response(200, content=np.full(4410, 1000, dtype='<i2').tobytes(), headers={'content-type': 'audio/pcm'})
    transport(monkeypatch, handler)
    result = await generations.generate_speech(models.GenerationRequest(profile_id='p', text='hello', engine='openrouter'), db)
    assert result.model_size == api.DEFAULT_MODEL
    await queue.pop(0)
    db.expire_all()
    row = db.get(Generation, result.id)
    assert row.status == 'completed', row.error
    from backend import config
    info = sf.info(str(config.resolve_storage_path(row.audio_path)))
    assert info.samplerate == 44100 and info.frames == 4410
    from backend.database import GenerationVersion
    assert db.query(GenerationVersion).filter_by(generation_id=row.id).count() == 1
    row.status = 'failed'
    db.commit()
    api.update_settings({'model': 'fish-audio/drama-3'})
    await generations.retry_generation(row.id, db)
    await queue.pop(0)
    assert [r['model'] for r in requests] == [api.DEFAULT_MODEL, api.DEFAULT_MODEL]
    assert requests[0]['input_references'] == requests[1]['input_references']
    db.expire_all()
    assert db.get(Generation, row.id).status == 'completed'


@pytest.mark.asyncio
async def test_pipeline_caps_failure_records_safe_error_without_post(pipeline, monkeypatch):
    from backend import models
    from backend.routes import generations
    from backend.database import Generation
    db, queue, _ = pipeline
    calls = []
    def handler(request):
        calls.append(request.method)
        return httpx.Response(200, json={'data': {'endpoints': [{'status': 0, 'supports_voice_cloning': False}]}})
    transport(monkeypatch, handler)
    result = await generations.generate_speech(models.GenerationRequest(profile_id='p', text='hello', engine='openrouter'), db)
    await queue.pop(0)
    db.expire_all()
    row = db.get(Generation, result.id)
    assert row.status == 'failed'
    assert 'cannot clone' in row.error
    assert calls == ['GET']


@pytest.mark.asyncio
async def test_optional_transcript_cloud_only(pipeline, monkeypatch):
    from backend.services import profiles
    from backend.database import VoiceProfile
    db, _, audio = pipeline
    monkeypatch.setattr(profiles, 'validate_and_load_reference_audio', lambda p, **kw: (True, None, np.ones(24000) * .1, 24000))
    result = await profiles.add_profile_sample('p', str(audio), '', db)
    assert result.reference_text == ''
    profile = db.get(VoiceProfile, 'p')
    profile.default_engine = 'qwen'
    db.commit()
    with pytest.raises(ValueError, match='transcript'):
        await profiles.add_profile_sample('p', str(audio), '', db)
    with pytest.raises(ValueError, match='transcripts'):
        await profiles.create_voice_prompt_for_profile('p', db, engine='qwen')


@pytest.mark.asyncio
async def test_no_local_weight_requirements(monkeypatch):
    from backend import backends
    monkeypatch.setattr(backends, 'get_tts_model_configs', lambda: [])
    assert not backends.engine_retries_runaway('openrouter')
    await backends.ensure_model_cached_or_raise('openrouter', api.DEFAULT_MODEL)
    await backends.load_engine_model('openrouter', api.DEFAULT_MODEL)
    assert backends.get_tts_backend_for_engine('openrouter').is_loaded()


@pytest.mark.asyncio
async def test_reference_over_limit_rejected_without_clipping(tmp_path):
    path = tmp_path / 'long.wav'
    sf.write(path, np.full(8000 * 181, .2), 8000)
    with pytest.raises(api.OpenRouterError, match='exceeds 180 seconds'):
        await OpenRouterBackend().create_voice_prompt(str(path), 'full transcript')


@pytest.mark.asyncio
@pytest.mark.parametrize('raw,mime', [(b'1', 'audio/pcm'), (b'{}', 'application/json'), (b'', 'audio/pcm')])
async def test_invalid_audio_fails_without_retry(monkeypatch, raw, mime):
    monkeypatch.setattr(api, 'require_cloning', AsyncMock())
    mocked = AsyncMock(return_value=(raw, mime))
    monkeypatch.setattr(api, 'request', mocked)
    backend = OpenRouterBackend()
    await backend.load_model(api.DEFAULT_MODEL)
    with pytest.raises(api.OpenRouterError, match='invalid audio'):
        await backend.generate('hi', {'input_references': [{'type': 'input_audio'}]})
    assert mocked.await_count == 1


@pytest.mark.asyncio
async def test_pipeline_cancel_preserves_failed_state(pipeline, monkeypatch):
    from backend import models
    from backend.routes import generations
    from backend.database import Generation
    db, queue, _ = pipeline
    monkeypatch.setattr(api, 'require_cloning', AsyncMock())
    monkeypatch.setattr(api, 'request', AsyncMock(side_effect=asyncio.CancelledError()))
    result = await generations.generate_speech(models.GenerationRequest(profile_id='p', text='hello', engine='openrouter'), db)
    await queue.pop(0)
    db.expire_all()
    row = db.get(Generation, result.id)
    assert row.status == 'failed'
    assert row.error == 'Generation cancelled'


def test_key_check_preserves_401_without_echo(client, monkeypatch):
    api.update_settings({'api_key': 'fake-secret'})
    transport(monkeypatch, lambda request: httpx.Response(401, text='fake-secret'))
    result = client.post('/settings/openrouter/test', json={})
    assert result.status_code == 401
    assert 'Replace it in Settings' in result.json()['detail']
    assert 'fake-secret' not in result.text


def test_exact_free_default_and_explicit_paid_labels():
    assert api.DEFAULT_MODEL == 'heygen/heygen-voice-1-turbo:free'
    assert 'Free' in api.MODEL_NAMES[api.DEFAULT_MODEL]
    assert 'Paid' in api.MODEL_NAMES['heygen/heygen-voice-1-turbo']
    assert api.status()['model'] == api.DEFAULT_MODEL


@pytest.mark.asyncio
@pytest.mark.parametrize('missing_text', [False, True])
async def test_all_samples_in_chronological_order_preserve_tones(pipeline, missing_text):
    from datetime import datetime, timedelta
    from backend.database import ProfileSample
    from backend.services import profiles
    db, _, first_path = pipeline
    db.query(ProfileSample).delete()
    # UUID-like order differs from time order; equal timestamps use id order.
    base = datetime(2026, 1, 1)
    specs = [('z', 220, 48000, base), ('b', 660, 44100, base + timedelta(seconds=1)),
             ('a', 440, 16000, base + timedelta(seconds=1))]
    for ident, hz, sr, created in specs:
        path = first_path.parent / f'{ident}.wav'
        tone = .2 * np.sin(2 * np.pi * hz * np.arange(sr) / sr)
        sf.write(path, np.column_stack([tone, tone]), sr)
        text = '' if missing_text and ident == 'a' else ident
        db.add(ProfileSample(id=ident, profile_id='p', audio_path=str(path),
                             reference_text=text, created_at=created))
    db.commit()
    for _ in range(2):
        prompt = await profiles.create_voice_prompt_for_profile('p', db, engine='openrouter')
        refs = prompt['input_references']
        assert sum(r['type'] == 'input_audio' for r in refs) == 1
        raw = base64.b64decode(refs[0]['input_audio']['data'].split(',')[1])
        info = sf.info(io.BytesIO(raw))
        audio, sr = sf.read(io.BytesIO(raw))
        assert sr == 24000 and info.channels == 1 and info.subtype == 'PCM_16'
        assert len(audio) == 81600  # three seconds plus two 0.2-second gaps
        for start, expected in [(0, 220), (28800, 440), (57600, 660)]:
            segment = audio[start + 2400:start + 21600]
            freq = np.fft.rfftfreq(len(segment), 1 / sr)[np.argmax(abs(np.fft.rfft(segment)))]
            assert abs(freq - expected) < 2
        assert np.max(abs(audio[24000:28800])) == 0
        if missing_text:
            assert len(refs) == 1
        else:
            assert refs[1] == {'type': 'text', 'text': 'z a b'}


@pytest.mark.asyncio
@pytest.mark.parametrize('seconds,accepted', [(89.9, True), (90, False)])
async def test_combined_limit_includes_gaps(tmp_path, seconds, accepted):
    path = tmp_path / 'tone.wav'
    sf.write(path, np.full(round(24000 * seconds), .2), 24000)
    backend = OpenRouterBackend()
    if accepted:
        audio, text = await backend.combine_voice_prompts([str(path)] * 2, ['one', 'two'])
        assert len(audio) == 180 * 24000
        assert text == 'one two'
    else:
        with pytest.raises(api.OpenRouterError, match='including gaps'):
            await backend.combine_voice_prompts([str(path)] * 2, ['one', 'two'])


@pytest.mark.asyncio
@pytest.mark.parametrize('engine,seconds,accepted', [
    ('qwen', 30, True), ('qwen', 30.1, False),
    ('openrouter', 30.1, True), ('openrouter', 180, True), ('openrouter', 180.1, False),
])
async def test_upload_duration_boundaries(pipeline, engine, seconds, accepted):
    from backend.database import VoiceProfile
    from backend.services import profiles
    db, _, path = pipeline
    db.get(VoiceProfile, 'p').default_engine = engine
    db.commit()
    sf.write(path, .2 * np.sin(2 * np.pi * 440 * np.arange(round(24000 * seconds)) / 24000), 24000)
    if accepted:
        result = await profiles.add_profile_sample('p', str(path), 'matching transcript', db)
        from backend import config
        assert sf.info(config.resolve_storage_path(result.audio_path)).duration == seconds
    else:
        with pytest.raises(ValueError, match='Audio too long'):
            await profiles.add_profile_sample('p', str(path), 'matching transcript', db)


@pytest.mark.asyncio
@pytest.mark.parametrize('code', [402, 404, 429, 503])
async def test_free_model_failure_never_falls_back_to_paid(monkeypatch, code):
    api.update_settings({'api_key': 'fake-test-key'})
    requests = []
    def handler(request):
        if request.method == 'GET':
            return httpx.Response(200, json={'data': {'endpoints': [{'status': 0, 'supports_voice_cloning': True}]}})
        requests.append(json.loads(request.content))
        return httpx.Response(code)
    transport(monkeypatch, handler)
    backend = OpenRouterBackend()
    await backend.load_model()
    with pytest.raises(api.OpenRouterError):
        await backend.generate('hello', {'input_references': [{'type': 'input_audio'}]})
    assert [r['model'] for r in requests] == ['heygen/heygen-voice-1-turbo:free']
    assert api.status()['model'] == 'heygen/heygen-voice-1-turbo:free'


def test_sample_timestamp_migration_is_repeatable(tmp_path):
    from sqlalchemy import create_engine, inspect, text
    from backend.database.migrations import _migrate_profile_samples
    engine = create_engine('sqlite:///' + str(tmp_path / 'legacy.sqlite'))
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE profile_samples (id TEXT PRIMARY KEY, reference_text TEXT)'))
        conn.execute(text("INSERT INTO profile_samples VALUES ('old', 'words')"))
    for _ in range(2):
        _migrate_profile_samples(engine, inspect(engine), {'profile_samples'})
    with engine.connect() as conn:
        assert conn.execute(text('SELECT id, reference_text, created_at FROM profile_samples')).one() == (
            'old', 'words', '1970-01-01 00:00:00')
    engine.dispose()


@pytest.mark.asyncio
async def test_combined_cache_invalidated_on_text_and_sample_changes(pipeline):
    from backend import config
    from backend.database import ProfileSample
    from backend.services import profiles
    db, _, path = pipeline
    db.add(ProfileSample(id='b', profile_id='p', audio_path=str(path), reference_text='second'))
    db.commit()
    await profiles.create_voice_prompt_for_profile('p', db, engine='openrouter')
    cached = list(config.get_cache_dir().glob('combined_p_*.wav'))
    assert len(cached) == 1
    await profiles.update_profile_sample('a', 'first', db)
    assert not cached[0].exists()
    prompt = await profiles.create_voice_prompt_for_profile('p', db, engine='openrouter')
    assert prompt['input_references'][1]['text'] == 'first second'
    await profiles.delete_profile_sample('b', db)
    assert not list(config.get_cache_dir().glob('combined_p_*.wav'))


@pytest.mark.asyncio
async def test_aggregate_over_limit_fails_before_any_provider_request(pipeline, monkeypatch):
    from backend import models
    from backend.database import ProfileSample, Generation
    from backend.routes import generations
    db, queue, path = pipeline
    sf.write(path, np.full(90 * 24000, .2), 24000)
    db.add(ProfileSample(id='b', profile_id='p', audio_path=str(path), reference_text=''))
    db.commit()
    request = AsyncMock(side_effect=AssertionError('No provider requests allowed'))
    monkeypatch.setattr(api, 'request', request)
    result = await generations.generate_speech(models.GenerationRequest(profile_id='p', text='hello', engine='openrouter'), db)
    await queue.pop(0)
    db.expire_all()
    row = db.get(Generation, result.id)
    assert row.status == 'failed'
    assert 'exceeds 180 seconds including gaps' in row.error
    request.assert_not_called()
