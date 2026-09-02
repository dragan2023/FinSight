import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.model_config.models import ProfileInput
from backend.app.settings import Settings, load_settings
from backend.app.storage import Storage


def authorize(client):
    client.headers['X-FinSight-Session'] = client.get('/api/system/session', headers={'X-FinSight-Client': 'web'}).json()['token']


def completed(content='OK', finish='stop', **extra):
    return {'id': 'offline', 'object': 'chat.completion', 'created': 1, 'model': 'offline',
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': content, **extra}, 'finish_reason': finish}]}



def offline_payload(**overrides):
    return {'name': '离线测试模型', 'base_url': 'https://model.example/v1', 'model': 'offline-model',
            'api_key': 'unit-test-secret-only', 'active': True, 'temperature': 0.2,
            'max_tokens': 1024, 'timeout': 20, **overrides}


def test_official_catalog_and_key_only_save(client):
    catalog = client.get('/api/models/providers').json()
    expected = {'deepseek': 'deepseek-v4-flash-vision-exp', 'glm': 'glm-5.3-flash', 'qwen': 'qwen3.8-flash'}
    assert {p['id']: p['default_model'] for p in catalog['providers']} == expected
    for provider, model in expected.items():
        result = client.post('/api/models', json={'provider': provider, 'api_key': 'offline-secret', 'active': True})
        assert result.status_code == 201, result.text
        saved = result.json()
        assert saved['model'] == model
        assert saved['options']['thinking'] == 'enabled'
        assert 'image' in saved['options']['input_modalities']
        assert 'offline-secret' not in result.text
        resolved = client.app.state.models.resolve()
        assert resolved.provider == provider
        assert resolved.options.model_dump() == saved['options']


@pytest.mark.parametrize('provider,options', [
    ('glm', {'thinking': 'disabled'}),
    ('glm', {'thinking': 'enabled', 'thinking_budget': 1024}),
    ('glm', {'thinking': 'enabled', 'input_modalities': ['audio']}),
    ('deepseek', {'thinking': 'disabled', 'reasoning_effort': 'high'}),
    ('deepseek', {'thinking': 'enabled', 'input_modalities': ['video']}),
    ('qwen', {'thinking': 'enabled', 'reasoning_effort': 'high'}),
    ('qwen', {'thinking': 'enabled', 'video_fps': 2, 'input_modalities': []}),
    ('qwen', {'thinking': 'enabled', 'input_modalities': ['image'], 'image_detail': 'low'}),
])
def test_reject_unsupported_combinations(client, provider, options):
    assert client.post('/api/models', json={'provider': provider, 'options': options}).status_code == 422


def test_omni_cannot_enable_thinking_and_official_cannot_override_url(client):
    assert client.post('/api/models', json={'provider': 'qwen', 'model': 'qwen3.5-omni-plus', 'options': {'thinking': 'enabled'}}).status_code == 422
    assert client.post('/api/models', json={'provider': 'deepseek', 'base_url': 'https://other.example/v1'}).status_code == 422
    assert client.post('/api/models', json={'provider': 'qwen', 'model': 'unverified-model'}).status_code == 422


def test_qwen_region_switch_requires_fresh_credential(client):
    saved = client.post('/api/models', json={'provider': 'qwen', 'api_key': 'offline-secret'}).json()
    data = {'provider': 'qwen', 'options': {**saved['options'], 'region': 'singapore', 'workspace_id': 'example-space'}}
    url = f"/api/models/{saved['id']}"
    assert client.put(url, json=data).json()['error']['category'] == 'endpoint_changed'
    changed = client.put(url, json={**data, 'api_key': 'replacement-offline'}).json()
    assert changed['base_url'] == 'https://example-space.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1'
    assert client.post(url + '/activate', json={}).status_code == 200
    assert client.app.state.models.resolve().options.region == 'singapore'


@pytest.mark.parametrize('provider,option_patch', [
    ('deepseek', {'reasoning_effort': 'max', 'image_detail': 'low'}),
    ('glm', {'reasoning_effort': 'low'}),
    ('qwen', {'thinking_budget': 3072}),
])
def test_wire_format_and_reply_does_not_expose_reasoning(settings, provider, option_patch, caplog):
    requests = []
    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        assert request.headers['authorization'] == 'Bearer offline-secret'
        assert 'offline-secret' not in request.content.decode()
        assert 'extra_body' not in body
        image = body['messages'][0]['content'][0]
        assert image['type'] == 'image_url'
        assert image['image_url']['url'] == 'https://public.example/chart.png'
        if provider == 'deepseek':
            assert request.url == 'https://api.deepseek.com/chat/completions'
            assert body['thinking'] == {'type': 'enabled'}
            assert body['reasoning_effort'] == 'max'
            assert 'temperature' not in body
            assert image['image_url']['detail'] == 'low'
            assert body['max_tokens'] == 393216
        elif provider == 'glm':
            assert request.url == 'https://open.bigmodel.cn/api/paas/v4/chat/completions'
            assert body['thinking'] == {'type': 'enabled', 'clear_thinking': False}
            assert body['reasoning_effort'] == 'low'
            assert body['temperature'] == 1
            assert body['max_tokens'] == 131072
        else:
            assert body['enable_thinking'] is True
            assert body['thinking_budget'] == 3072
            assert body['max_completion_tokens'] == 131072
            assert 'max_tokens' not in body and 'thinking' not in body
        return httpx.Response(200, json=completed('可展示的回复', reasoning_content='private-reasoning'))
    with TestClient(create_app(settings, transport=httpx.MockTransport(handler)), base_url='http://127.0.0.1') as client:
        authorize(client)
        defaults = ProfileInput(provider=provider).options.model_dump()
        assert client.post('/api/models', json={'provider': provider, 'api_key': 'offline-secret', 'active': True, 'options': {**defaults, **option_patch}}).status_code == 201
        request = {'prompt': '解释图表', 'confirm_external': True, 'media': {'kind': 'image', 'url': 'https://public.example/chart.png'}}
        result = client.post('/api/models/chat', json=request)
        assert result.status_code == 200, result.text
        assert result.json()['content'] == '可展示的回复'
        assert 'private-reasoning' not in result.text + caplog.text
    assert len(requests) == 1


def test_multimodal_gates_and_unconfirmed_calls_never_reach_provider(settings):
    def no_network(request):
        pytest.fail('Invalid requests must not reach the provider')
    with TestClient(create_app(settings, transport=httpx.MockTransport(no_network)), base_url='http://127.0.0.1') as client:
        authorize(client)
        client.post('/api/models', json={'provider': 'glm', 'api_key': 'offline-secret', 'active': True})
        assert client.post('/api/models/chat', json={'prompt': 'hello'}).status_code == 409
        assert client.post('/api/models/chat', json={'prompt': 'hello', 'confirm_external': True, 'media': {'kind': 'audio', 'url': 'https://public.example/a.wav'}}).status_code == 422
        for url in ['http://public.example/chart.png', 'https://127.0.0.1/x', 'https://private.local/x', 'https://user:secret@public.example/x']:
            assert client.post('/api/models/chat', json={'prompt': 'hello', 'confirm_external': True, 'media': {'kind': 'image', 'url': url}}).status_code == 422
        result = client.post('/api/models/test', json={'mode': 'connectivity', 'confirm_external': True})
        assert result.status_code == 200 and result.json()['supported'] is False


def test_glm_tools_use_auto_and_keep_thinking_budget(settings):
    def handler(request):
        body = json.loads(request.content)
        assert body['tool_choice'] == 'auto'
        assert 'strict' not in body['tools'][0]['function']
        assert body['thinking']['type'] == 'enabled'
        assert body['max_tokens'] == 131072
        return httpx.Response(200, json=completed(None, finish='tool_calls', tool_calls=[
            {'id': 'test', 'type': 'function', 'function': {'name': 'connection_probe', 'arguments': '{"value":"ok"}'}}]))
    with TestClient(create_app(settings, transport=httpx.MockTransport(handler)), base_url='http://127.0.0.1') as client:
        authorize(client)
        client.post('/api/models', json={'provider': 'glm', 'api_key': 'offline-secret', 'active': True})
        assert client.post('/api/models/test', json={'mode': 'tools', 'confirm_external': True}).json()['supported']


def test_video_fps_is_at_content_part_level(settings):
    def handler(request):
        body = json.loads(request.content)
        part = body['messages'][0]['content'][0]
        assert part == {'type': 'video_url', 'video_url': {'url': 'https://public.example/clip.mp4'}, 'fps': 0.5}
        return httpx.Response(200, json=completed())
    with TestClient(create_app(settings, transport=httpx.MockTransport(handler)), base_url='http://127.0.0.1') as client:
        authorize(client)
        client.post('/api/models', json={'provider': 'qwen', 'api_key': 'offline-secret', 'active': True,
                                       'options': {'thinking': 'enabled', 'input_modalities': ['video'], 'video_fps': 0.5}})
        assert client.post('/api/models/chat', json={'prompt': 'summary', 'confirm_external': True, 'media': {'kind': 'video', 'url': 'https://public.example/clip.mp4'}}).status_code == 200


@pytest.mark.parametrize('finish', ['stop', 'length', None])
def test_omni_streaming_audio_and_incomplete_stream(settings, finish):
    def handler(request):
        body = json.loads(request.content)
        assert body['model'] == 'qwen3.5-omni-plus'
        assert body['stream'] is True
        assert body['modalities'] == ['text']
        assert 'enable_thinking' not in body
        assert body['messages'][0]['content'][0] == {'type': 'input_audio', 'input_audio': {'data': 'https://public.example/a.wav', 'format': 'wav'}}
        chunks = [{'choices': [{'index': 0, 'delta': {'content': '模型回复', 'reasoning_content': 'never-show'}, 'finish_reason': None}]}]
        if finish:
            chunks.append({'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}]})
        chunks.append({'choices': [], 'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}})
        stream = ''.join('data: ' + json.dumps({'id': 'test', 'model': 'qwen3.5-omni-plus', 'object': 'chat.completion.chunk', 'created': 1, **chunk}) + '\n\n' for chunk in chunks) + 'data: [DONE]\n\n'
        return httpx.Response(200, headers={'Content-Type': 'text/event-stream'}, content=stream.encode())
    with TestClient(create_app(settings, transport=httpx.MockTransport(handler)), base_url='http://127.0.0.1') as client:
        authorize(client)
        client.post('/api/models', json={'provider': 'qwen', 'model': 'qwen3.5-omni-plus', 'api_key': 'offline-secret', 'active': True})
        result = client.post('/api/models/chat', json={'prompt': 'describe', 'confirm_external': True, 'media': {'kind': 'audio', 'url': 'https://public.example/a.wav'}})
        assert result.status_code == (200 if finish else 502), result.text
        if finish:
            assert result.json()['truncated'] is (finish == 'length')
        assert 'never-show' not in result.text


def test_explicit_first_save_initializes_and_survives_restart(tmp_path, monkeypatch):
    monkeypatch.delenv('FINSIGHT_ENCRYPTION_KEY', raising=False)
    settings = Settings(project_root=tmp_path)
    def no_network(request):
        pytest.fail('Saving a profile must not call a model')
    with TestClient(create_app(settings, transport=httpx.MockTransport(no_network)), base_url='http://127.0.0.1') as client:
        authorize(client)
        assert not (tmp_path / '.env').exists()
        data = {'provider': 'deepseek', 'api_key': 'offline-secret', 'active': True}
        assert client.post('/api/models', json=data).status_code == 409
        assert not (tmp_path / '.env').exists()
        saved = client.post('/api/models', json={**data, 'initialize_encryption': True})
        assert saved.status_code == 201, saved.text
        assert client.get('/api/system/status').json()['encryption']['can_save']
        saved_id = saved.json()['id']
    with TestClient(create_app(load_settings(tmp_path), transport=httpx.MockTransport(no_network)), base_url='http://127.0.0.1') as client:
        authorize(client)
        assert client.get('/api/models').json()[0]['id'] == saved_id
        assert client.app.state.models.resolve().api_key.get_secret_value() == 'offline-secret'
    (tmp_path / '.env').write_text('', encoding='utf-8')
    with TestClient(create_app(settings), base_url='http://127.0.0.1') as client:
        authorize(client)
        assert not client.get('/api/system/status').json()['encryption']['can_initialize']
        assert client.post('/api/models', json={**data, 'initialize_encryption': True}).status_code == 409
        assert not (tmp_path / '.env').read_text()


def test_schema_v1_migration_preserves_ciphertext_and_settings(tmp_path):
    path = tmp_path / 'old.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE model_profiles (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, base_url TEXT NOT NULL, model TEXT NOT NULL, encrypted_api_key TEXT, temperature REAL NOT NULL, max_tokens INTEGER NOT NULL, timeout REAL NOT NULL, active INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)')
        db.execute("INSERT INTO model_profiles VALUES ('existing', 'existing', 'https://model.example/v1', 'legacy-model', 'unchanged-cipher', 0.4, 2048, 60, 1, 'created', 'updated')")
        db.execute('PRAGMA user_version = 1')
    Storage(path).initialize()
    Storage(path).initialize()
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        row = dict(db.execute('SELECT * FROM model_profiles').fetchone())
        assert row['encrypted_api_key'] == 'unchanged-cipher'
        assert row['provider'] == 'custom'
        assert row['options_json'] == '{}'
        assert row['active'] == 1 and row['temperature'] == 0.4
        # Maximum-output migration: legacy ciphertext and sampling settings survive,
        # official presets adopt the documented maximum with the old value kept.
        assert row['output_policy'] == 'custom' and row['legacy_max_tokens'] == 2048
        assert db.execute('PRAGMA user_version').fetchone()[0] == 15
        assert db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='api_profiles'").fetchone()
        assert db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_tasks'").fetchone()

def test_omni_streamed_tool_arguments_are_assembled_before_validation(settings):
    def handler(request):
        body = json.loads(request.content)
        assert body['stream'] and body['tool_choice'] == 'auto'
        parts = [
            {'tool_calls': [{'index': 0, 'id': 'probe', 'type': 'function', 'function': {'name': 'connection_probe', 'arguments': '{"value":'}}]},
            {'tool_calls': [{'index': 0, 'function': {'arguments': '"ok"}'}}]},
            {},
        ]
        chunks = [{'id': 'test', 'model': 'qwen3.5-omni-plus', 'object': 'chat.completion.chunk', 'created': 1,
                   'choices': [{'index': 0, 'delta': part, 'finish_reason': 'tool_calls' if index == 2 else None}]}
                  for index, part in enumerate(parts)]
        stream = ''.join('data: ' + json.dumps(chunk) + '\n\n' for chunk in chunks) + 'data: [DONE]\n\n'
        return httpx.Response(200, headers={'Content-Type': 'text/event-stream'}, content=stream.encode())
    with TestClient(create_app(settings, transport=httpx.MockTransport(handler)), base_url='http://127.0.0.1') as client:
        authorize(client)
        client.post('/api/models', json={'provider': 'qwen', 'model': 'qwen3.5-omni-plus', 'api_key': 'offline-secret', 'active': True})
        result = client.post('/api/models/test', json={'mode': 'tools', 'confirm_external': True})
        assert result.status_code == 200 and result.json()['supported'], result.text


@pytest.mark.parametrize('provider', ['deepseek', 'qwen'])
def test_disabling_thinking_changes_official_request_and_sampling(settings, provider):
    def handler(request):
        body = json.loads(request.content)
        assert body['temperature'] == 0.4
        if provider == 'deepseek':
            assert body['thinking']['type'] == 'disabled'
            assert 'reasoning_effort' not in body
        else:
            assert body['enable_thinking'] is False
            assert 'thinking_budget' not in body
        return httpx.Response(200, json=completed())
    with TestClient(create_app(settings, transport=httpx.MockTransport(handler)), base_url='http://127.0.0.1') as client:
        authorize(client)
        client.post('/api/models', json={'provider': provider, 'api_key': 'offline-secret', 'active': True,
                                       'temperature': 0.4, 'options': {'thinking': 'disabled'}})
        assert client.post('/api/models/chat', json={'prompt': 'hi', 'confirm_external': True}).status_code == 200



def test_maximum_policy_sends_documented_limits_without_second_param(settings):
    expected = {'deepseek': ('max_tokens', 393216), 'glm': ('max_tokens', 131072),
                'qwen': ('max_completion_tokens', 131072)}
    for provider, (param, value) in expected.items():
        requests = []

        def handler(request, param=param, value=value):
            requests.append(request)
            body = json.loads(request.content)
            assert body[param] == value
            assert not (set(body) & ({'max_tokens', 'max_completion_tokens'} - {param}))
            assert 'extra_body' not in body
            return httpx.Response(200, json=completed())

        with TestClient(create_app(settings, transport=httpx.MockTransport(handler)), base_url='http://127.0.0.1') as client:
            authorize(client)
            assert client.post('/api/models', json={'provider': provider, 'api_key': 'offline-secret', 'active': True}).status_code == 201
            assert client.post('/api/models/chat', json={'prompt': 'hi', 'confirm_external': True}).status_code == 200
        assert len(requests) == 1


def test_omni_keeps_verified_streaming_output_param(settings):
    def handler(request):
        body = json.loads(request.content)
        assert body['max_tokens'] == 65536
        assert 'max_completion_tokens' not in body
        assert body['stream'] is True
        chunks = [{'choices': [{'index': 0, 'delta': {'content': '回复'}, 'finish_reason': None}]},
                  {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]}]
        stream = ''.join('data: ' + json.dumps({'id': 't', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'qwen3.5-omni-plus', **c}) + '\n\n' for c in chunks) + 'data: [DONE]\n\n'
        return httpx.Response(200, headers={'Content-Type': 'text/event-stream'}, content=stream.encode())
    with TestClient(create_app(settings, transport=httpx.MockTransport(handler)), base_url='http://127.0.0.1') as client:
        authorize(client)
        client.post('/api/models', json={'provider': 'qwen', 'model': 'qwen3.5-omni-plus', 'api_key': 'offline-secret', 'active': True})
        assert client.post('/api/models/chat', json={'prompt': 'hi', 'confirm_external': True}).status_code == 200


def test_thinking_budget_conflict_is_rejected_not_trimmed(client):
    authorize(client)
    # 131072 total output minus the required 16384 body reserve allows at most 114688.
    assert client.post('/api/models', json={'provider': 'qwen', 'api_key': 'offline-secret',
                                            'options': {'thinking': 'enabled', 'thinking_budget': 114689}}).status_code == 422
    saved = client.post('/api/models', json={'provider': 'qwen', 'api_key': 'offline-secret',
                                             'options': {'thinking': 'enabled', 'thinking_budget': 114688}})
    assert saved.status_code == 201, saved.text


def test_view_reports_effective_output_and_legacy_value(client):
    authorize(client)
    official = client.post('/api/models', json={'provider': 'deepseek', 'api_key': 'offline-secret'}).json()
    assert official['output_policy'] == 'maximum'
    assert official['effective_output'] == {'param': 'max_tokens', 'value': 393216, 'policy': 'maximum',
                                            'requested': 393216, 'estimated_input_tokens': None, 'reason': None}
    assert official['legacy_max_tokens'] == 8192
    custom = client.post('/api/models', json={**offline_payload(), 'max_tokens': 1024}).json()
    assert custom['output_policy'] == 'custom'
    assert custom['effective_output']['value'] == 1024 and custom['effective_output']['policy'] == 'custom'


def test_custom_maximum_requires_declared_vendor_limits(client):
    authorize(client)
    unknown = client.post('/api/models', json={**offline_payload(), 'output_policy': 'maximum'})
    assert unknown.status_code == 422
    declared = client.post('/api/models', json={**offline_payload(), 'output_policy': 'maximum',
                                                'options': {'context_limit': 262144, 'max_output': 65536}})
    assert declared.status_code == 201, declared.text
    assert declared.json()['effective_output']['value'] == 65536


def test_custom_output_exceeds_old_global_cap(settings):
    def handler(request):
        body = json.loads(request.content)
        assert body['max_tokens'] == 100000
        return httpx.Response(200, json=completed())
    with TestClient(create_app(settings, transport=httpx.MockTransport(handler)), base_url='http://127.0.0.1') as client:
        authorize(client)
        client.post('/api/models', json={**offline_payload(), 'max_tokens': 100000})
        assert client.post('/api/models/chat', json={'prompt': 'hi', 'confirm_external': True}).status_code == 200


def test_v3_to_v4_migration_adopts_maximum_for_official(tmp_path):
    path = tmp_path / 'v3.db'
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE model_profiles (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, base_url TEXT NOT NULL,
                   model TEXT NOT NULL, encrypted_api_key TEXT, temperature REAL NOT NULL, max_tokens INTEGER NOT NULL,
                   timeout REAL NOT NULL, active INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                   provider TEXT NOT NULL DEFAULT 'custom', options_json TEXT NOT NULL DEFAULT '{}')''')
        db.execute("INSERT INTO model_profiles VALUES ('official', '官方', 'https://api.deepseek.com', 'deepseek-v4-pro', 'cipher', 0.2, 8192, 120, 1, 'c', 'u', 'deepseek', '{}')")
        db.execute("INSERT INTO model_profiles VALUES ('customrow', '自建', 'https://model.example/v1', 'legacy-model', NULL, 0.2, 4096, 60, 0, 'c', 'u', 'custom', '{}')")
        db.execute("CREATE TABLE api_profiles (id TEXT PRIMARY KEY, provider TEXT NOT NULL, name TEXT NOT NULL UNIQUE, encrypted_api_key TEXT, enabled INTEGER NOT NULL, options_json TEXT NOT NULL DEFAULT '{}', credential_version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        db.execute('PRAGMA user_version = 3')
    Storage(path).initialize()
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        official = dict(db.execute("SELECT * FROM model_profiles WHERE id='official'").fetchone())
        custom = dict(db.execute("SELECT * FROM model_profiles WHERE id='customrow'").fetchone())
        assert official['output_policy'] == 'maximum' and official['legacy_max_tokens'] == 8192
        assert custom['output_policy'] == 'custom' and custom['legacy_max_tokens'] == 4096
        assert db.execute('PRAGMA user_version').fetchone()[0] == 15
