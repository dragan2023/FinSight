import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.settings import Settings

pytestmark = pytest.mark.integration


def test_no_keys_startup_is_offline_and_closes_pool(tmp_path):
    app = create_app(Settings(project_root=tmp_path), transport=httpx.MockTransport(
        lambda request: pytest.fail("Health and startup must be offline")))
    assert not (tmp_path / "data").exists()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/health").json()["status"] == "ok"
        state = client.get("/api/system/status").json()
        assert state["encryption"] == {"status": "missing", "can_save": False, "can_initialize": True}
        assert state["model"]["status"] == "model_unconfigured"
        assert state["market_data"]["status"] == "not_configured"
        assert not app.state.http.is_closed
    assert app.state.http.is_closed
    assert (tmp_path / "data/db/finsight.db").exists()


def test_session_host_origin_content_type_and_payload_size(client, payload):
    assert client.get("/health", headers={"host": "evil.example"}).status_code == 400
    assert client.get("/api/system/session").status_code == 403
    assert client.get("/api/system/session", headers={"X-FinSight-Client": "web", "Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/system/session", headers={"X-FinSight-Client": "web", "Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post("/api/models", json=payload, headers={"X-FinSight-Session": "wrong"}).status_code == 403
    assert client.post("/api/models", json=payload, headers={"Origin": "null"}).status_code == 403
    assert client.post("/api/models", json=payload, headers={"Content-Type": "text/plain"}).status_code == 415
    assert client.post("/api/models", content='x' * 32769).status_code == 413
    assert client.get("/api/models").json() == []
    allowed = client.options("/api/models", headers={"Origin": "http://127.0.0.1:5173", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "X-FinSight-Session, Content-Type"})
    assert allowed.status_code == 200
    forbidden = client.options("/api/models", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert forbidden.status_code == 400


def test_restart_preserves_profiles_rotates_session(settings, payload):
    app = create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        token = client.get("/api/system/session", headers={"X-FinSight-Client": "web"}).json()["token"]
        client.headers["X-FinSight-Session"] = token
        response = client.post("/api/models", json=payload)
        assert response.status_code == 201
        saved_id = response.json()["id"]
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        assert client.get("/api/models", headers={"X-FinSight-Session": token}).status_code == 403
        new_token = client.get("/api/system/session", headers={"X-FinSight-Client": "web"}).json()["token"]
        assert new_token != token
        rows = client.get("/api/models", headers={"X-FinSight-Session": new_token}).json()
        assert rows[0]["id"] == saved_id
        assert rows[0]["key_status"] == "configured"


def test_validation_never_echoes_input(client, payload, caplog):
    for invalid in ({"base_url": "https://user:exposed-secret@model.example/v1"},
                    {"api_key": {"key": "exposed-secret"}}, {"temperature": "exposed-secret"},
                    {"api_key": "exposed-secret", "clear_api_key": True}):
        response = client.post("/api/models", json={**payload, **invalid})
        assert response.status_code == 422
        assert response.json()["error"]["request_id"]
        assert "exposed-secret" not in response.text
        assert payload["api_key"] not in response.text
        assert response.headers["cache-control"] == "no-store"
    response = client.post("/api/models", content='{"api_key":"exposed-secret",broken')
    assert response.status_code == 422 and "exposed-secret" not in response.text
    assert "exposed-secret" not in caplog.text


def test_missing_encryption_refuses_save_but_preserves_existing(settings, client, payload):
    row = client.post("/api/models", json=payload).json()
    without_key = settings.model_copy(update={"encryption_key": Settings().encryption_key})
    with TestClient(create_app(without_key), base_url="http://127.0.0.1") as reopened:
        token = reopened.get("/api/system/session", headers={"X-FinSight-Client": "web"}).json()["token"]
        reopened.headers["X-FinSight-Session"] = token
        assert reopened.get(f'/api/models/{row["id"]}').json()["key_status"] == "locked"
        assert reopened.put(f'/api/models/{row["id"]}', json=payload).status_code == 409
        assert not (settings.project_root / ".env").exists()
    assert client.get(f'/api/models/{row["id"]}').json()["key_status"] == "configured"
