import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.app.credentials import CredentialRoot
from backend.app.main import create_app
from backend.app.model_config.local_secrets import initialize
from backend.app.settings import Settings, load_settings
from backend.app.storage import Storage

pytestmark = pytest.mark.integration


def authorize(client):
    token = client.get('/api/system/session', headers={'X-FinSight-Client': 'web'}).json()['token']
    client.headers['X-FinSight-Session'] = token


def hexin_payload(**overrides):
    return {'provider': 'hexin_finance', 'name': '同花顺主源', 'api_key': 'offline-integration-key', **overrides}


def offline(settings):
    def no_network(request):
        pytest.fail('Credential scenarios must stay offline')
    return TestClient(create_app(settings, transport=httpx.MockTransport(no_network)), base_url='http://127.0.0.1')


def test_api_profile_first_save_initializes_shared_root_and_model_saves(tmp_path, monkeypatch):
    monkeypatch.delenv('FINSIGHT_ENCRYPTION_KEY', raising=False)
    settings = Settings(project_root=tmp_path)
    with offline(settings) as client:
        authorize(client)
        assert not (tmp_path / '.env').exists()
        refused = client.post('/api/integrations/profiles', json=hexin_payload())
        assert refused.status_code == 409
        assert not (tmp_path / '.env').exists()
        saved = client.post('/api/integrations/profiles', json={**hexin_payload(), 'initialize_encryption': True})
        assert saved.status_code == 201, saved.text
        status = client.get('/api/system/status').json()
        assert status['encryption']['can_save'] and not status['encryption']['can_initialize']
        # One shared root: model profiles can save immediately after API-side initialization.
        model = client.post('/api/models', json={
            'provider': 'deepseek', 'api_key': 'offline-secret', 'initialize_encryption': True})
        assert model.status_code == 201, model.text
        saved_id, model_id = saved.json()['id'], model.json()['id']
    with offline(load_settings(tmp_path)) as client:
        authorize(client)
        assert client.get('/api/integrations/profiles').json()[0]['id'] == saved_id
        assert client.get('/api/models').json()[0]['id'] == model_id


def test_lost_root_key_locks_api_and_model_ciphertexts_and_blocks_reinit(tmp_path, monkeypatch):
    monkeypatch.delenv('FINSIGHT_ENCRYPTION_KEY', raising=False)
    settings = Settings(project_root=tmp_path)
    with offline(settings) as client:
        authorize(client)
        client.post('/api/integrations/profiles', json={**hexin_payload(), 'initialize_encryption': True})
        client.post('/api/models', json={'provider': 'deepseek', 'api_key': 'offline-secret'})
    wrong = Settings(project_root=tmp_path, encryption_key=SecretStr(Fernet.generate_key().decode()))
    with offline(wrong) as client:
        authorize(client)
        status = client.get('/api/system/status').json()
        # A wrong but well-formed key can encrypt anew; it must not reinitialize and locks both stores.
        assert status['encryption']['can_save'] is True
        assert status['encryption']['can_initialize'] is False
        assert client.get('/api/integrations/profiles').json()[0]['key_status'] == 'locked'
        assert client.get('/api/models').json()[0]['key_status'] == 'locked'


def test_api_only_ciphertext_blocks_new_root_key_generation(tmp_path, monkeypatch):
    monkeypatch.delenv('FINSIGHT_ENCRYPTION_KEY', raising=False)
    settings = Settings(project_root=tmp_path, encryption_key=SecretStr(Fernet.generate_key().decode()))
    with offline(settings) as client:
        authorize(client)
        assert client.post('/api/integrations/profiles', json=hexin_payload()).status_code == 201
        assert client.get('/api/models').json() == []
    # model_profiles is empty; the api_profiles ciphertext alone must block a new root key.
    monkeypatch.delenv('FINSIGHT_ENCRYPTION_KEY', raising=False)
    assert not (tmp_path / '.env').exists()
    with pytest.raises(RuntimeError, match='已有加密记录'):
        initialize(tmp_path)
    assert not (tmp_path / '.env').exists()
    root = CredentialRoot(Storage(settings.db_path), settings)
    assert root.has_ciphertexts()
    assert root.can_initialize() is False


def test_disabled_profile_stays_disabled_and_listed(client):
    authorize(client)
    view = client.post('/api/integrations/profiles',
                       json={**hexin_payload(), 'enabled': False}).json()
    # Status tracks credential+test state; the enabled flag stays separate.
    assert view['enabled'] is False and view['status'] == 'saved_unverified'
