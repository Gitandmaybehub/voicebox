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
    assert len(result['models']) == 3
    assert not any(m['supports_voice_cloning'] for m in result['models'])
    assert result['models'][1]['error']
    with pytest.raises(api.OpenRouterError, match='cannot clone'):
        await OpenRouterBackend().generate('hi', {"input_references": [{"type": "input_audio"}]})


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
    monkeypatch.setattr(profiles, 'validate_and_load_reference_audio', lambda p: (True, None, np.ones(24000) * .1, 24000))
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
async def test_reference_bounded_and_cropped_transcript_omitted(tmp_path):
    path = tmp_path / 'long.wav'
    sf.write(path, np.full(8000 * 181, .2), 8000)
    prompt, _ = await OpenRouterBackend().create_voice_prompt(str(path), 'full untrimmed transcript')
    assert len(prompt['input_references']) == 1
    raw = base64.b64decode(prompt['input_references'][0]['input_audio']['data'].split(',')[1])
    assert len(raw) < 15 * 1024 * 1024
    assert sf.info(io.BytesIO(raw)).duration == 180


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
