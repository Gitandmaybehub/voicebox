"""Check the real app's middleware setup, including mounted MCP writes."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.app import _configure_cors


@pytest.fixture
def guarded(monkeypatch):
    monkeypatch.delenv('VOICEBOX_CORS_ORIGINS', raising=False)
    app = FastAPI()
    _configure_cors(app)
    calls = []

    @app.api_route('/{path:path}', methods=['POST', 'PUT', 'DELETE'])
    def write(path: str):
        calls.append(path)
        return {'ok': True}

    mcp = FastAPI()
    @mcp.post('/')
    def tool():
        calls.append('mcp')
        return {'ok': True}
    # Put mount first so the test really exercises a mounted application.
    app.mount('/mcp', mcp)
    app.router.routes.insert(0, app.router.routes.pop())
    return TestClient(app, base_url='http://localhost:17493'), calls


@pytest.mark.parametrize('path,method', [('/generate','POST'),('/speak','POST'),('/mcp/','POST'),('/settings/openrouter','PUT'),('/settings/openrouter/test','POST')])
@pytest.mark.parametrize('origin', ['https://evil.example', 'null', 'http://localhost.evil.example', 'http://localhost:5173@evil.example', 'http://localhost:5173/path'])
def test_external_browser_write_never_reaches_handler(guarded, path, method, origin):
    client, calls = guarded
    response = client.request(method, path, headers={'origin': origin}, json={})
    assert response.status_code == 403
    assert calls == []


@pytest.mark.parametrize('origin', ['tauri://localhost', 'http://tauri.localhost', 'https://tauri.localhost', 'http://localhost:5173', 'http://127.0.0.1:5173', 'http://localhost:17493'])
def test_native_dev_and_same_origin_writes_work(guarded, origin):
    client, calls = guarded
    assert client.post('/generate', headers={'origin': origin}, json={}).status_code == 200
    assert calls == ['generate']


def test_native_no_origin_and_browser_metadata(guarded):
    client, calls = guarded
    assert client.post('/mcp/', json={}).status_code == 200
    assert client.post('/speak', headers={'sec-fetch-site': 'cross-site'}, json={}).status_code == 403
    assert calls == ['mcp']


def test_same_origin_remote_deployment(guarded):
    client, calls = guarded
    assert client.post('https://voice.example/generate', headers={'origin':'https://voice.example'}, json={}).status_code == 200
    assert calls == ['generate']
