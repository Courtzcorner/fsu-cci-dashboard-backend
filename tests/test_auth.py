import time

import jwt

from app.config import get_settings
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME, login


def test_successful_login_returns_exact_response_shape(client, admin_user):
    response = client.post("/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
    assert response.status_code == 200

    body = response.json()
    assert set(body.keys()) == {"access_token", "token_type", "expires_in", "user"}
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 3600
    assert body["user"] == {"username": "admin", "role": "admin"}


def test_jwt_contains_username_and_role_claims(client, admin_user):
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    settings = get_settings()
    payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    assert payload["sub"] == "admin"
    assert payload["role"] == "admin"


def test_invalid_password_returns_401(client, admin_user):
    response = client.post("/login", json={"username": ADMIN_USERNAME, "password": "wrong-password"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password"


def test_unknown_username_returns_401(client):
    response = client.post("/login", json={"username": "ghost", "password": "whatever123"})
    assert response.status_code == 401


def test_login_response_never_leaks_password_hash(client, admin_user):
    response = client.post("/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
    body_text = response.text
    assert "password_hash" not in body_text
    assert "$2b$" not in body_text


def test_expired_token_is_rejected(client, admin_user, organization):
    settings = get_settings()
    expired_payload = {
        "sub": "admin",
        "role": "admin",
        "iat": time.time() - 7200,
        "exp": time.time() - 3600,
    }
    expired_token = jwt.encode(expired_payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)

    response = client.get(
        "/alumni-data",
        params={"organization": "fsu-cci"},
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert response.status_code == 401
    assert "expired" in response.json()["detail"].lower()


def test_missing_token_returns_401(client, organization):
    response = client.get("/alumni-data", params={"organization": "fsu-cci"})
    assert response.status_code == 401


def test_invalid_token_returns_401(client, organization):
    response = client.get(
        "/alumni-data",
        params={"organization": "fsu-cci"},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401
