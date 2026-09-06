"""Phase 2 RBAC tests — login, token enforcement, role matrix."""
from fastapi.testclient import TestClient

from backend.api.main import app
from conftest import auth_headers

client = TestClient(app)


def test_login_success_roles():
    for username, password, role in [
        ("admin", "supervisor123", "supervisor"),
        ("analyst", "analyst123", "analyst"),
        ("investigator", "investigator123", "investigator"),
    ]:
        r = client.post("/login", json={"username": username, "password": password})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["role"] == role
        assert body["token_type"] == "bearer"
        assert body["access_token"]


def test_login_bad_password():
    r = client.post("/login", json={"username": "admin", "password": "wrong"})
    assert r.status_code == 401


def test_login_unknown_user():
    r = client.post("/login", json={"username": "mallory", "password": "x"})
    assert r.status_code == 401


def test_unauthenticated_blocked():
    assert client.get("/graph?day=58").status_code == 401
    assert client.get("/bursts").status_code == 401
    assert client.get("/ask", params={"q": "Why was X1 flagged"}).status_code == 401
    assert client.get("/leads").status_code == 401


def test_health_open():
    assert client.get("/health").status_code == 200


def test_analyst_cannot_view_graph():
    h = auth_headers("analyst")
    assert client.get("/graph?day=58", headers=h).status_code == 403
    assert client.get("/bursts", headers=h).status_code == 403
    assert client.get("/ask", params={"q": "Why was X1 flagged"}, headers=h).status_code == 403


def test_analyst_can_upload_and_map():
    # upload + mapping endpoints accept analyst (file I/O not exercised here;
    # 404 proves auth+role passed and routing reached the handler)
    h = auth_headers("analyst")
    r = client.get("/investigations/nope/files", headers=h)
    assert r.status_code in (404, 200)


def test_investigator_can_upload_and_map():
    h = auth_headers("investigator")
    r = client.get("/investigations/nope/files", headers=h)
    assert r.status_code in (404, 200)


def test_investigator_can_view_graph_and_ask():
    h = auth_headers("investigator")
    assert client.get("/graph?day=58", headers=h).status_code == 200
    r = client.get("/ask", params={"q": "Why was X1 flagged"}, headers=h)
    assert r.status_code == 200


def test_supervisor_full_access():
    h = auth_headers("supervisor")
    assert client.get("/graph?day=58", headers=h).status_code == 200
    assert client.get("/bursts", headers=h).status_code == 200
    assert client.get("/leads", headers=h).status_code == 200


def test_me_returns_session():
    h = auth_headers("investigator")
    r = client.get("/me", headers=h)
    assert r.status_code == 200
    assert r.json() == {"username": "investigator", "role": "investigator", "name": "Investigator"}


def test_tampered_token_rejected():
    h = dict(auth_headers("supervisor"))
    h["Authorization"] += "tampered"
    assert client.get("/leads", headers=h).status_code == 401


def _unique_user(prefix="judge"):
    import uuid as _uuid
    return f"{prefix}_{_uuid.uuid4().hex[:8]}"


def test_register_investigator_and_use():
    username = _unique_user()
    r = client.post("/register", json={"username": username, "password": "secret123", "role": "investigator", "name": "Judge Demo"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["role"] == "investigator" and body["token_type"] == "bearer"
    h = {"Authorization": f"Bearer {body['access_token']}"}
    assert client.get("/graph?day=58", headers=h).status_code == 200
    r_files = client.get("/investigations/nope/files", headers=h)
    assert r_files.status_code in (404, 200)


def test_register_analyst_can_upload_path():
    username = _unique_user("analyst")
    r = client.post("/register", json={"username": username, "password": "secret123", "role": "analyst"})
    assert r.status_code == 201, r.text
    assert r.json()["role"] == "analyst"


def test_register_duplicate_rejected():
    username = _unique_user()
    assert client.post("/register", json={"username": username, "password": "secret123", "role": "investigator"}).status_code == 201
    r = client.post("/register", json={"username": username, "password": "secret123", "role": "investigator"})
    assert r.status_code == 409


def test_register_rejects_supervisor_and_weak_input():
    assert client.post("/register", json={"username": _unique_user(), "password": "secret123", "role": "supervisor"}).status_code == 400
    assert client.post("/register", json={"username": "ab", "password": "secret123", "role": "investigator"}).status_code == 400
    assert client.post("/register", json={"username": _unique_user(), "password": "123", "role": "investigator"}).status_code == 400


def _make_investigation(headers):
    r = client.post("/investigations", headers=headers, json={"name": "Delete me", "description": "ephemeral test case"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_delete_requires_auth():
    iid = _make_investigation(auth_headers("supervisor"))
    assert client.delete(f"/investigations/{iid}").status_code == 401
    # cleanup as supervisor
    assert client.delete(f"/investigations/{iid}", headers=auth_headers("supervisor")).status_code == 200


def test_delete_allowed_for_all_roles():
    for role in ("analyst", "investigator", "supervisor"):
        iid = _make_investigation(auth_headers("supervisor"))
        r = client.delete(f"/investigations/{iid}", headers=auth_headers(role))
        assert r.status_code == 200, (role, r.text)


def test_delete_removes_filesystem_and_is_idempotent_404():
    from backend.ingestion.store import ROOT
    iid = _make_investigation(auth_headers("supervisor"))
    assert (ROOT / iid).exists()
    r = client.delete(f"/investigations/{iid}", headers=auth_headers("supervisor"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] is True and body["investigation_id"] == iid
    assert body["neo4j_nodes_deleted"] == 0  # Neo4j unavailable in CI
    assert not (ROOT / iid).exists()
    assert client.get(f"/investigations/{iid}", headers=auth_headers("supervisor")).status_code == 404
    assert client.delete(f"/investigations/{iid}", headers=auth_headers("supervisor")).status_code == 404


def test_delete_rejects_path_traversal():
    r = client.delete("/investigations/..%2F..", headers=auth_headers("supervisor"))
    assert r.status_code in (404, 422)
