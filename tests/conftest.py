"""Shared auth helpers for API tests (Phase 2 RBAC)."""
from fastapi.testclient import TestClient

from backend.api.main import app

CREDS = {
    "supervisor": ("admin", "supervisor123"),
    "analyst": ("analyst", "analyst123"),
    "investigator": ("investigator", "investigator123"),
}

_tokens = {}


def auth_headers(role: str = "supervisor") -> dict:
    """Return Authorization headers for a demo user (tokens cached per role)."""
    if role not in _tokens:
        username, password = CREDS[role]
        r = TestClient(app).post("/login", json={"username": username, "password": password})
        assert r.status_code == 200, r.text
        _tokens[role] = r.json()["access_token"]
    return {"Authorization": f"Bearer {_tokens[role]}"}
