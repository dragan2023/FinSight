import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.settings import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(project_root=tmp_path, encryption_key=Fernet.generate_key().decode())


@pytest.fixture
def client(settings):
    def no_network(request):
        raise AssertionError("Offline tests must not make external requests")

    with TestClient(create_app(settings, transport=httpx.MockTransport(no_network)),
                    base_url="http://127.0.0.1:8000") as client:
        token = client.get("/api/system/session", headers={"X-FinSight-Client": "web"}).json()["token"]
        client.headers.update({"X-FinSight-Session": token, "Content-Type": "application/json"})
        yield client


@pytest.fixture
def payload():
    return {"name": "离线测试模型", "base_url": "https://model.example/v1", "model": "offline-model",
            "api_key": "unit-test-secret-only", "active": True, "temperature": 0.2, "max_tokens": 1024, "timeout": 20}
