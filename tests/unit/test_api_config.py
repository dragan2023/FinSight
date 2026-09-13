import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.app.main import create_app
from backend.app.settings import Settings


def authorize(client):
    token = client.get('/api/system/session', headers={'X-FinSight-Client': 'web'}).json()['token']
    client.headers['X-FinSight-Session'] = token


def profile_payload(**overrides):
    return {'provider': 'hexin_finance', 'name': '同花顺主源', 'api_key': 'offline-integration-key', **overrides}


def offline_app(settings, **extra):
    def no_network(request):
        pytest.fail('Catalog and credential operations must stay offline')
    return TestClient(create_app(settings, transport=httpx.MockTransport(no_network)), base_url='http://127.0.0.1')


def test_catalog_lists_selected_services_without_network(client):
    authorize(client)
    result = client.get('/api/integrations/providers')
    assert result.status_code == 200
    body = result.json()
    assert {item['id'] for item in body['providers']} == {'hexin_finance', 'bocha', 'qwen_embedding', 'local_sources'}
    assert body['environment'] == {'finance': False, 'llm': False}
    for item in body['providers']:
        assert item['implementation'] in ('planned', 'available')
        assert item['docs_url'] == '' or item['docs_url'].startswith('https://')
    hexin = next(item for item in body['providers'] if item['id'] == 'hexin_finance')
    assert hexin['implementation'] == 'available'
    assert hexin['credential']['kind'] == 'api_key'
    assert {'prices', 'valuations', 'financials'} <= {c['id'] for c in hexin['capabilities']}


def test_save_mask_and_status_without_faking_connectivity(client):
    authorize(client)
    created = client.post('/api/integrations/profiles', json=profile_payload())
    assert created.status_code == 201, created.text
    view = created.json()
    assert view['key_status'] == 'configured'
    assert view['masked_api_key'] == '••••••••'
    assert 'offline-integration-key' not in created.text and 'encrypted_api_key' not in created.text
    # Stage 02 adapter is available: configured key reports saved_unverified
    # until a capability test runs; nothing is called without confirmation.
    assert view['status'] == 'saved_unverified' and view['implementation'] == 'available'
    assert client.post(f"/api/integrations/profiles/{view['id']}/test",
                       json={'capability': 'prices'}).status_code == 409
    assert client.post(f"/api/integrations/profiles/{view['id']}/test",
                       json={'capability': 'nonexistent'}).status_code == 422


def test_no_key_services_reject_credential_storage(client):
    authorize(client)
    assert client.post('/api/integrations/profiles',
                       json={'provider': 'local_sources', 'name': '本地资料'}).status_code == 422


def test_blank_edit_keeps_key_clear_and_version_checks(client):
    authorize(client)
    view = client.post('/api/integrations/profiles', json=profile_payload()).json()
    url = f"/api/integrations/profiles/{view['id']}"
    kept = client.put(url, json={**profile_payload(), 'api_key': '   ',
                                 'credential_version': view['credential_version']})
    assert kept.status_code == 200 and kept.json()['key_status'] == 'configured'
    stale = client.put(url, json={**profile_payload(), 'api_key': 'replacement-key', 'credential_version': 99})
    assert stale.status_code == 409 and stale.json()['error']['category'] == 'version_conflict'
    replaced = client.put(url, json={**profile_payload(), 'api_key': 'replacement-key',
                                     'credential_version': view['credential_version']})
    assert replaced.status_code == 200
    assert replaced.json()['credential_version'] == view['credential_version'] + 1
    assert client.request('DELETE', url, params={'credential_version': 1}, json={}).status_code == 409
    assert client.request('DELETE', url, params={'credential_version': view['credential_version'] + 1},
                          json={}).status_code == 204
    assert client.get('/api/integrations/profiles').json() == []


def test_explicit_clear_removes_credential(client):
    authorize(client)
    view = client.post('/api/integrations/profiles', json=profile_payload()).json()
    cleared = client.put(f"/api/integrations/profiles/{view['id']}",
                         json={**profile_payload(), 'api_key': None, 'clear_api_key': True,
                               'credential_version': view['credential_version']})
    assert cleared.status_code == 200
    body = cleared.json()
    assert body['key_status'] == 'empty' and body['masked_api_key'] is None
    assert body['credential_version'] == view['credential_version'] + 1


def test_provider_switch_requires_fresh_credential(client):
    authorize(client)
    view = client.post('/api/integrations/profiles', json=profile_payload()).json()
    url = f"/api/integrations/profiles/{view['id']}"
    switched = client.put(url, json={'provider': 'bocha', 'name': '博查补充',
                                     'credential_version': view['credential_version']})
    assert switched.status_code == 409 and switched.json()['error']['category'] == 'provider_changed'
    with_key = client.put(url, json={'provider': 'bocha', 'name': '博查补充', 'api_key': 'bocha-token',
                                     'credential_version': view['credential_version']})
    assert with_key.status_code == 200 and with_key.json()['provider'] == 'bocha'


def test_options_size_and_unknown_provider_rejected(client):
    authorize(client)
    assert client.post('/api/integrations/profiles',
                       json=profile_payload(options={'key': 'x' * 5000})).status_code == 422
    assert client.post('/api/integrations/profiles',
                       json=profile_payload(provider='brave')).status_code == 422


def test_finance_environment_import_is_explicit_and_idempotent(client, settings):
    authorize(client)
    missing = client.post('/api/integrations/import-environment', json={'service': 'finance'})
    assert missing.status_code == 409 and missing.json()['error']['category'] == 'environment_missing'
    env = Settings(project_root=settings.project_root,
                   encryption_key=SecretStr(settings.encryption_key.get_secret_value()),
                   finance_api_key=SecretStr('env-finance-key'))
    with offline_app(env) as local:
        authorize(local)
        imported = local.post('/api/integrations/import-environment', json={'service': 'finance'})
        assert imported.status_code == 200, imported.text
        body = imported.json()
        assert body['target'] == 'integration' and body['key_status'] == 'configured'
        assert 'env-finance-key' not in imported.text
        rows = local.get('/api/integrations/profiles').json()
        assert len(rows) == 1
        assert rows[0]['options']['imported_from'] == 'environment'
        assert rows[0]['options']['source_variable'] == 'HITHINK_FINANCE_API_KEY'
        assert local.post('/api/integrations/import-environment', json={'service': 'finance'}).json()['id'] == body['id']
        assert len(local.get('/api/integrations/profiles').json()) == 1


def test_llm_environment_import_creates_model_profile(client, settings):
    authorize(client)
    missing = client.post('/api/integrations/import-environment', json={'service': 'llm'})
    assert missing.status_code == 409 and missing.json()['error']['category'] == 'environment_missing'
    env = Settings(project_root=settings.project_root,
                   encryption_key=SecretStr(settings.encryption_key.get_secret_value()),
                   llm_base_url='https://env.example/v1', llm_model='env-model',
                   llm_api_key=SecretStr('env-llm-key'))
    with offline_app(env) as local:
        authorize(local)
        imported = local.post('/api/integrations/import-environment', json={'service': 'llm'})
        assert imported.status_code == 200, imported.text
        assert imported.json()['target'] == 'model'
        assert 'env-llm-key' not in imported.text
        profiles = local.get('/api/models').json()
        assert len(profiles) == 1 and profiles[0]['name'] == '环境导入模型'
        assert profiles[0]['active'] is False
        # Import is not activation: no hidden fallback for business calls.
        assert local.get('/api/system/status').json()['model']['status'] == 'no_active_model'


def test_invalid_llm_environment_is_reported_without_echo(settings):
    env = Settings(project_root=settings.project_root,
                   encryption_key=SecretStr(settings.encryption_key.get_secret_value()),
                   llm_base_url='https://user:leak@env.example/v1', llm_model='env-model',
                   llm_api_key=SecretStr('env-llm-key'))
    with offline_app(env) as local:
        authorize(local)
        result = local.post('/api/integrations/import-environment', json={'service': 'llm'})
        assert result.status_code == 409 and result.json()['error']['category'] == 'invalid_environment'
        assert 'leak' not in result.text


def test_capability_test_results_are_bound_to_credential_version(client):
    authorize(client)
    view = client.post('/api/integrations/profiles', json=profile_payload()).json()
    service = client.app.state.integrations
    service.repository.save_tests(view['id'], [
        {'capability': 'prices', 'status': 'ok', 'message': 'historical pass'}])
    row = service.repository.get(view['id'])
    assert row['tests'][0]['credential_version'] == view['credential_version']
    # Replacing the key invalidates results bound to the old version.
    updated = client.put(f"/api/integrations/profiles/{view['id']}",
                         json={**profile_payload(), 'api_key': 'second-key',
                               'credential_version': view['credential_version']}).json()
    assert service.repository.get(view['id'])['tests'] == []
    assert updated['credential_version'] == view['credential_version'] + 1


def test_options_roundtrip_and_decode_guards(client):
    authorize(client)
    options = {'budget': 5, 'enable_extract': True, 'note': '中文备注'}
    view = client.post('/api/integrations/profiles', json=profile_payload(options=options)).json()
    assert view['options'] == options
    service = client.app.state.integrations
    broken = service.repository.get(view['id'])
    broken['options_json'] = '{broken'
    assert service.repository.decode_options(broken) == {}
