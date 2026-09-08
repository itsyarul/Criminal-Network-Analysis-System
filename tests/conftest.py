"""Shared auth helpers for API tests (Phase 2 RBAC)."""
import pytest
from fastapi.testclient import TestClient

import backend.ingestion.store as store_mod
import backend.api.main as main_mod
from backend.api.main import app

CREDS = {
    "supervisor": ("admin", "supervisor123"),
    "analyst": ("analyst", "analyst123"),
    "investigator": ("investigator", "investigator123"),
}

_tokens = {}


@pytest.fixture(scope="session", autouse=True)
def isolate_investigations_directory(tmp_path_factory):
    """Isolate all test investigation folders into a temporary directory so data/investigations/ remains clean."""
    temp_dir = tmp_path_factory.mktemp("test_investigations")
    orig_store_root = store_mod.ROOT
    orig_main_root = main_mod.INV_ROOT

    store_mod.ROOT = temp_dir
    main_mod.INV_ROOT = temp_dir

    yield temp_dir

    store_mod.ROOT = orig_store_root
    main_mod.INV_ROOT = orig_main_root


def auth_headers(role: str = "supervisor") -> dict:
    """Return Authorization headers for a demo user (tokens cached per role)."""
    if role not in _tokens:
        username, password = CREDS[role]
        r = TestClient(app).post("/login", json={"username": username, "password": password})
        assert r.status_code == 200, r.text
        _tokens[role] = r.json()["access_token"]
    return {"Authorization": f"Bearer {_tokens[role]}"}
