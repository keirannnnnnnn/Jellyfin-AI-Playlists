import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.auth import create_session_token, COOKIE_NAME
from app.database import init_db


@pytest.fixture(autouse=True)
def setup_test_db():
    init_db()


def test_unauthenticated_redirects():
    client = TestClient(app)
    resp = client.get("/dashboard", follow_redirects=False)
    assert resp.status_code == 303
    assert "/login" in resp.headers["location"]


def test_login_flow():
    client = TestClient(app)

    # Wrong password
    resp = client.post("/login", data={"password": "WrongPassword", "next": "/dashboard"})
    assert resp.status_code == 200
    assert "Invalid password" in resp.text

    # Default password
    resp = client.post("/login", data={"password": "Password123", "next": "/dashboard"}, follow_redirects=False)
    assert resp.status_code == 303
    assert COOKIE_NAME in resp.cookies


def test_authenticated_dashboard_and_pages():
    client = TestClient(app)
    token = create_session_token()
    client.cookies.set(COOKIE_NAME, token)

    # Test pages render successfully
    assert client.get("/dashboard").status_code == 200
    assert client.get("/playlists").status_code == 200
    assert client.get("/trigger").status_code == 200
    assert client.get("/logs").status_code == 200
    assert client.get("/stats").status_code == 200
    assert client.get("/settings").status_code == 200


def test_api_toggle_user():
    client = TestClient(app)
    token = create_session_token()
    client.cookies.set(COOKIE_NAME, token)

    resp = client.post("/api/users/toggle", json={"user_id": "test_uid", "enabled": False})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False
