"""Public discovery, cache and model safety. All requests use offline fixtures."""
import asyncio
import copy
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.services import openrouter as api
from backend.routes.openrouter import router


@pytest.fixture
def discovery(monkeypatch, tmp_path):
    monkeypatch.setattr(api.config, 'get_data_dir', lambda: tmp_path)
    monkeypatch.setattr(api, 'settings_path', lambda: tmp_path / 'private.json')
    clock = [datetime(2026, 11, 9, tzinfo=timezone.utc)]
    monkeypatch.setattr(api, '_now', lambda: clock[0])
    ids = [api.DEFAULT_MODEL, 'heygen/heygen-voice-1-turbo'] + [f'new/model-{i}' for i in range(15)]
    catalog, rankings = [], []
    for i, ident in enumerate(ids):
        base, _, variant = ident.partition(':')
        permanent = base + '-20261101' + (':' + variant if variant else '')
        catalog.append({'slug': base, 'permaslug': base + '-20261101', 'name': ident,
                        'description': 'Public speech model', 'endpoint': {
                            'model_variant_slug': ident, 'model_variant_permaslug': permanent,
                            'pricing_json': {'tts:characters': '0' if variant == 'free' else '0.01'}}})
        rankings.append({'variant_permaslug': permanent, 'count': 1000 - i * 10,
                         'date': '2026-11-08 00:00:00'})
    # Deliberately not sorted by popularity, just like the real feed.
    rankings.reverse()
    feeds = {api.CATALOG_URL: {'data': {'models': catalog}}, api.RANKINGS_URL: {'data': rankings}}
    calls = []
    async def fetch(url):
        calls.append(url)
        await asyncio.sleep(0)
        return copy.deepcopy(feeds[url])
    monkeypatch.setattr(api, '_public_json', fetch)
    caps = AsyncMock(return_value=(True, True))
    monkeypatch.setattr(api, 'model_capability', caps)
    return feeds, calls, clock, caps


@pytest.mark.asyncio
async def test_group_order_new_models_and_distinct_free_variants(discovery):
    feeds, calls, _, caps = discovery
    rows = feeds[api.RANKINGS_URL]['data']
    rows.append({**rows[0], 'count': 1000})  # unseen model-14 becomes first
    result = await api.list_models()
    assert len(result['models']) == 15
    assert result['models'][0]['id'] == 'new/model-14'
    assert result['models'][0]['weekly_requests'] == 1840
    assert [r['rank'] for r in result['models']] == list(range(1, 16))
    assert [r['id'] for r in result['models']][1:3] == [api.DEFAULT_MODEL, 'heygen/heygen-voice-1-turbo']
    assert result['models'][1]['pricing'] == {'tts:characters': '0'}
    assert result['selected_model']['id'] == api.DEFAULT_MODEL
    assert result['recommended_model'] == api.DEFAULT_MODEL
    assert result['source_url'] == api.SOURCE_URL
    assert result['fetched_at'] == '2026-11-09T00:00:00Z'
    assert result['data_date'] == '2026-11-08 00:00:00'
    assert result['stale'] is False
    assert caps.await_count == 15
    assert set(calls) == {api.RANKINGS_URL, api.CATALOG_URL}


@pytest.mark.asyncio
async def test_ttl_persistence_manual_refresh_and_stale(discovery, monkeypatch):
    feeds, calls, clock, caps = discovery
    first = await api.list_models()
    assert api._cache_path().is_file()
    clock[0] += timedelta(seconds=3599)
    assert await api.list_models() == first
    assert len(calls) == 2
    clock[0] += timedelta(seconds=1)
    second = await api.list_models()
    assert len(calls) == 4 and second['fetched_at'] != first['fetched_at']
    await api.list_models(refresh=True)
    assert len(calls) == 6
    raw_cache = api._cache_path().read_bytes()
    monkeypatch.setattr(api, '_public_json', AsyncMock(side_effect=RuntimeError('PRIVATE-SECRET')))
    stale = await api.list_models(refresh=True)
    assert stale['models'] == second['models'] and stale['stale']
    assert stale['error'] == api.CATALOG_ERROR
    assert 'PRIVATE-SECRET' not in json.dumps(stale)
    assert api._cache_path().read_bytes() == raw_cache


@pytest.mark.asyncio
async def test_pinned_legacy_selection_does_not_expand_top15(discovery):
    api.update_settings({'model': 'new/model-14', 'api_key': 'SECRET'})
    result = await api.list_models()
    assert len(result['models']) == 15
    assert 'new/model-14' not in [r['id'] for r in result['models']]
    assert result['selected_model']['id'] == 'new/model-14'
    assert result['selected_model']['rank'] is None
    assert result['selected_model']['weekly_requests'] is None
    assert result['selected_model']['supports_voice_cloning']
    assert 'SECRET' not in api._cache_path().read_text()
    assert api.status()['model'] == 'new/model-14'
    api.update_settings({'model': 'retired/model'})
    result = await api.list_models()
    assert not result['selected_model']['available']
    assert not result['selected_model']['supports_voice_cloning']
    assert result['selected_model']['rank'] is None


@pytest.mark.asyncio
async def test_cold_error_has_no_invented_rankings(discovery, monkeypatch):
    monkeypatch.setattr(api, '_public_json', AsyncMock(side_effect=ValueError('SECRET')))
    result = await api.list_models()
    assert result['models'] == [] and result['stale']
    assert result['fetched_at'] is None and result['data_date'] is None
    assert result['selected_model']['id'] == api.DEFAULT_MODEL
    assert not result['selected_model']['supports_voice_cloning']
    assert not api._cache_path().exists()
    assert 'SECRET' not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', ['count', 'mapping', 'date', 'short', 'variant'])
async def test_malformed_feed_retains_last_success(discovery, bad):
    feeds, _, _, _ = discovery
    first = await api.list_models()
    if bad == 'count':
        feeds[api.RANKINGS_URL]['data'][0]['count'] = float('nan')
    elif bad == 'date':
        feeds[api.RANKINGS_URL]['data'][0]['date'] = 'SECRET'
    elif bad == 'mapping':
        feeds[api.CATALOG_URL]['data']['models'] = []
    elif bad == 'short':
        feeds[api.RANKINGS_URL]['data'] = feeds[api.RANKINGS_URL]['data'][:3]
    else:
        feeds[api.CATALOG_URL]['data']['models'][0]['endpoint']['model_variant_permaslug'] = 'heygen/bad'
    result = await api.list_models(refresh=True)
    assert result['stale'] and result['models'] == first['models']
    assert 'SECRET' not in json.dumps(result)


@pytest.mark.asyncio
async def test_capability_errors_fail_closed_and_limit_concurrency(discovery, monkeypatch):
    active, maximum = 0, 0
    async def caps(model):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(.001)
        active -= 1
        if model == api.DEFAULT_MODEL:
            raise api.OpenRouterError('SECRET')
        return True, False
    monkeypatch.setattr(api, 'model_capability', caps)
    result = await api.list_models()
    assert maximum <= 4
    assert len(result['models']) == 15
    assert not any(r['supports_voice_cloning'] for r in result['models'])
    assert result['selected_model']['error'] == api.CAPABILITY_ERROR
    assert 'SECRET' not in json.dumps(result)


@pytest.mark.asyncio
async def test_concurrent_refreshes_coalesce(discovery):
    _, calls, _, caps = discovery
    results = await asyncio.gather(*(api.list_models(refresh=True) for _ in range(8)))
    assert len(calls) == 2 and caps.await_count == 15
    assert all(r == results[0] for r in results)


@pytest.mark.asyncio
async def test_malformed_cache_and_settings_are_safe(discovery, monkeypatch):
    await api.list_models()
    cache = json.loads(api._cache_path().read_text())
    cache['models'][0]['rank'] = 5
    api._cache_path().write_text(json.dumps(cache))
    assert api._read_cache() is None
    api.settings_path().write_text('["SECRET"]')
    monkeypatch.setattr(api, '_public_json', AsyncMock(side_effect=ValueError('SECRET')))
    result = await api.list_models()
    assert result['models'] == [] and result['selected_model'] is None
    assert 'SECRET' not in json.dumps(result)
    with pytest.raises(api.OpenRouterError):
        api.update_settings({'model': '../SECRET'})


def test_route_refresh_contract(discovery, monkeypatch):
    mocked = AsyncMock(return_value={'models': []})
    monkeypatch.setattr(api, 'list_models', mocked)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        assert client.get('/openrouter/models?refresh=true').status_code == 200
        mocked.assert_awaited_with(refresh=True)
        assert client.get('/openrouter/models').status_code == 200
        mocked.assert_awaited_with(refresh=False)


@pytest.mark.asyncio
async def test_public_transport_no_auth_redirects_or_external_origins(monkeypatch):
    original = httpx.AsyncClient
    seen = []
    def handler(request):
        seen.append(request)
        assert 'authorization' not in request.headers
        return httpx.Response(302, text='SECRET', headers={'location': 'https://evil.example'})
    def factory(**kwargs):
        assert kwargs['follow_redirects'] is False and kwargs['trust_env'] is False
        return original(**kwargs, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(api.httpx, 'AsyncClient', factory)
    monkeypatch.setattr(api, '_key', lambda: pytest.fail('No credentials allowed'))
    for url in [api.RANKINGS_URL, 'https://evil.example', api.BASE_URL + '/models/a/b?secret/endpoints']:
        with pytest.raises(api.OpenRouterError) as exc:
            await api._public_json(url)
        assert 'SECRET' not in str(exc.value)
    assert len(seen) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('outputs,status,clone,expected', [
    (['speech'], 0, True, (True, True)), (['audio'], 0, True, (False, False)),
    (['speech'], 1, True, (False, False)), (['speech'], 0, False, (True, False)),
    ([], 0, True, (False, False)),
])
async def test_capabilities_require_active_speech(monkeypatch, outputs, status, clone, expected):
    monkeypatch.setattr(api, '_public_json', AsyncMock(return_value={'data': {
        'architecture': {'output_modalities': outputs},
        'endpoints': [{'status': status, 'supports_voice_cloning': clone}]}}))
    assert await api.model_capability('new/unseen:free') == expected


@pytest.mark.parametrize('model', ['https://evil/a', 'a/../b', 'a/b?q=x', 'a/b#x', 'a/b/c', 'a/b\n', None, [], 'a/b%2f'])
def test_slug_validation_without_membership(model):
    with pytest.raises(api.OpenRouterError):
        api.validate_model(model)
    assert api.validate_model('new/unseen-1.2:free') == 'new/unseen-1.2:free'
