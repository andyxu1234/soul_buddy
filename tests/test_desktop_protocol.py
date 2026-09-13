"""Desktop bootstrap protocol (A09 / B11): one-time token + host guard."""
import pytest
from fastapi.testclient import TestClient
from soul_buddy.api.main import create_app


@pytest.fixture
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_bootstrap_wrong_token(client):
    r = client.get("/bootstrap?token=wrong", headers={"host": "127.0.0.1"})
    assert r.status_code == 401


def test_bootstrap_host_guard(client):
    token = client.app.state.runtime.bootstrap_token
    r = client.get(f"/bootstrap?token={token}", headers={"host": "evil.com:8000"})
    assert r.status_code == 400


def test_bootstrap_one_time(client):
    token = client.app.state.runtime.bootstrap_token
    r1 = client.get(f"/bootstrap?token={token}", headers={"host": "127.0.0.1"})
    assert r1.status_code == 200
    r2 = client.get(f"/bootstrap?token={token}", headers={"host": "127.0.0.1"})
    assert r2.status_code == 401
