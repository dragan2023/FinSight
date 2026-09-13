import pytest
from cryptography.fernet import Fernet
from dotenv import dotenv_values
from pydantic import SecretStr

from backend.app.credentials import CredentialRoot
from backend.app.errors import AppError
from backend.app.model_config.service import ModelService
from scripts.init_local_secrets import initialize


def test_crud_keys_activation_and_endpoint_change(client, payload):
    created = client.post("/api/models", json=payload)
    assert created.status_code == 201
    first = created.json()
    assert first["masked_api_key"] == "••••••••"
    assert "api_key" not in first and "encrypted_api_key" not in first
    row = client.app.state.models.repository.get(first["id"])
    original_ciphertext = row["encrypted_api_key"]
    assert payload["api_key"] not in original_ciphertext
    assert client.put(f'/api/models/{first["id"]}', json={**payload, "api_key": "   "}).status_code == 200
    assert client.app.state.models.repository.get(first["id"])["encrypted_api_key"] == original_ciphertext
    second = client.post("/api/models", json={**payload, "name": "第二模型", "model": "second-model"}).json()
    assert [p["id"] for p in client.get("/api/models").json() if p["active"]] == [second["id"]]
    assert client.app.state.models.resolve().model == "second-model"
    assert client.post(f'/api/models/{first["id"]}/activate', json={}).status_code == 200
    assert client.app.state.models.resolve().model == payload["model"]
    failed = client.put(f'/api/models/{first["id"]}', json={**payload, "api_key": "", "base_url": "https://other.example/v1"})
    assert failed.status_code == 409 and failed.json()["error"]["category"] == "endpoint_changed"
    cleared = client.put(f'/api/models/{first["id"]}', json={**payload, "api_key": "", "clear_api_key": True, "active": False})
    assert cleared.json()["key_status"] == "empty"
    assert client.post(f'/api/models/{first["id"]}/activate', json={}).status_code == 409
    assert client.request('DELETE', f'/api/models/{first["id"]}', json={}).status_code == 204
    assert client.get(f'/api/models/{first["id"]}').status_code == 404


def test_name_conflict_rolls_back_active_switch(client, payload):
    first = client.post("/api/models", json=payload).json()
    assert client.post("/api/models", json=payload).status_code == 409
    assert client.app.state.models.resolve().model == payload["model"]
    assert client.get("/api/models").json()[0]["id"] == first["id"]


def test_profile_limit(client, payload):
    for index in range(12):
        assert client.post("/api/models", json={**payload, "name": f"model-{index}"}).status_code == 201
    assert client.post("/api/models", json={**payload, "name": "over-limit"}).status_code == 409
    assert len(client.get("/api/models").json()) == 12


def test_wrong_root_key_locks_existing_profiles_without_fallback(client, settings, payload):
    client.post("/api/models", json=payload)
    wrong = settings.model_copy(update={"encryption_key": SecretStr(Fernet.generate_key().decode())})
    service = ModelService(client.app.state.models.repository, CredentialRoot(client.app.state.storage, wrong), wrong)
    assert service.list()[0].key_status == "locked"
    with pytest.raises(AppError, match="无法解密"):
        service.resolve()


def test_environment_config_requires_explicit_import(client, settings, payload):
    # Stage 01 contract: business calls never fall back to hidden environment keys.
    env_settings = settings.model_copy(update={"llm_base_url": "https://environment.example/v1",
                                               "llm_api_key": SecretStr("env-key"), "llm_model": "env-model"})
    service = ModelService(client.app.state.models.repository, client.app.state.models.root, env_settings)
    with pytest.raises(AppError) as error:
        service.resolve()
    assert error.value.category == "model_unconfigured"
    client.post("/api/models", json={**payload, "active": False})
    with pytest.raises(AppError) as error:
        service.resolve()
    assert error.value.category == "no_active_model"


def test_local_secret_initialization_is_explicit_idempotent_and_preserves_env(tmp_path, monkeypatch):
    monkeypatch.delenv("FINSIGHT_ENCRYPTION_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("# keep me\nLLM_MODEL=example\nFINSIGHT_ENCRYPTION_KEY=\n", encoding="utf-8")
    result = initialize(tmp_path)
    values = dotenv_values(env)
    assert values["LLM_MODEL"] == "example"
    Fernet(values["FINSIGHT_ENCRYPTION_KEY"].encode())
    assert values["FINSIGHT_ENCRYPTION_KEY"] not in result
    original = env.read_bytes()
    initialize(tmp_path)
    assert original == env.read_bytes()


def test_secret_initialization_refuses_existing_encrypted_records(client, payload, settings, monkeypatch):
    monkeypatch.delenv("FINSIGHT_ENCRYPTION_KEY", raising=False)
    client.post("/api/models", json=payload)
    with pytest.raises(RuntimeError, match="已有加密记录"):
        initialize(settings.project_root)
    assert not (settings.project_root / ".env").exists()


@pytest.mark.parametrize("address", ["http://remote.example/v1", "file:///etc/passwd", "https://test.example/v1?api_key=secret", "https://test.example/v1#secret"])
def test_unsafe_endpoints_rejected(client, payload, address):
    assert client.post("/api/models", json={**payload, "base_url": address}).status_code == 422
