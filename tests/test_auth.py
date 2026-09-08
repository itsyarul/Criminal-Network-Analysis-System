"""Access-control lifecycle tests — approval-based provisioning + role matrix."""
import uuid as _uuid

from fastapi.testclient import TestClient

from backend.api.main import app
from conftest import auth_headers

client = TestClient(app)

_CREATED = []


def _unique_user(prefix="officer"):
    return f"{prefix}_{_uuid.uuid4().hex[:8]}"


def _track(*usernames):
    _CREATED.extend(usernames)


def _drop_users(*usernames):
    from backend.auth import _load_registered, _save_registered

    registered = _load_registered()
    changed = False
    for u in usernames:
        if u in registered:
            del registered[u]
            changed = True
    if changed:
        _save_registered(registered)


def teardown_module():
    _drop_users(*_CREATED)


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
        assert body["status"] == "active"


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


def test_me_returns_full_profile():
    h = auth_headers("investigator")
    r = client.get("/me", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "investigator" and body["role"] == "investigator"
    assert body["status"] == "active"
    assert "department" in body and "badge_id" in body


def test_tampered_token_rejected():
    h = dict(auth_headers("supervisor"))
    h["Authorization"] += "tampered"
    assert client.get("/leads", headers=h).status_code == 401


# ---------------------------------------------------------------------------
# Approval lifecycle: request → pending → approve → active login
# ---------------------------------------------------------------------------

def test_request_access_creates_pending_without_token():
    username = _unique_user("singh")
    _track(username)
    r = client.post("/auth/request-access", json={
        "username": username, "password": "secret123", "role": "investigator",
        "name": "Inspector Singh", "badge_id": "CYBER-102",
        "department": "Cyber Crime Investigation Cell",
        "justification": "Assigned to syndicate case",
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "pending_approval"
    assert body["badge_id"] == "CYBER-102"
    assert "access_token" not in body
    assert "administrative review" in body["message"]


def test_pending_login_blocked_with_notice():
    username = _unique_user("pending")
    _track(username)
    assert client.post("/auth/request-access", json={
        "username": username, "password": "secret123", "role": "analyst",
    }).status_code == 201
    r = client.post("/login", json={"username": username, "password": "secret123"})
    assert r.status_code == 403, r.text
    assert "pending administrative authorization" in r.json()["detail"]


def test_approval_activates_login_to_role_dashboard():
    username = _unique_user("clear")
    _track(username)
    assert client.post("/auth/request-access", json={
        "username": username, "password": "secret123", "role": "investigator",
        "name": "Inspector Clear", "badge_id": "CYBER-103",
        "department": "Special Crime Branch",
    }).status_code == 201
    h = auth_headers("supervisor")
    r = client.post(f"/admin/users/{username}/approve", headers=h, json={})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"
    assert r.json()["approved_by"] == "admin"
    r = client.post("/login", json={"username": username, "password": "secret123"})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "investigator"
    token = r.json()["access_token"]
    assert client.get("/graph?day=58", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_rejected_login_reports_reason():
    username = _unique_user("denied")
    _track(username)
    assert client.post("/auth/request-access", json={
        "username": username, "password": "secret123", "role": "investigator",
    }).status_code == 201
    h = auth_headers("supervisor")
    r = client.post(f"/admin/users/{username}/reject", headers=h, json={"reason": "Unverified badge"})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    r = client.post("/login", json={"username": username, "password": "secret123"})
    assert r.status_code == 403, r.text
    assert "Unverified badge" in r.json()["detail"]


def test_suspend_blocks_login_and_reactivate_restores():
    username = _unique_user("patel")
    _track(username)
    h = auth_headers("supervisor")
    r = client.post("/admin/users", headers=h, json={
        "username": username, "password": "secret123", "role": "analyst",
        "name": "Analyst Patel", "badge_id": "ANL-207", "department": "Data Analytics Unit",
    })
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "active"
    assert client.post("/login", json={"username": username, "password": "secret123"}).status_code == 200
    r = client.patch(f"/admin/users/{username}/status", headers=h, json={"status": "suspended"})
    assert r.status_code == 200 and r.json()["status"] == "suspended"
    r = client.post("/login", json={"username": username, "password": "secret123"})
    assert r.status_code == 403 and "suspended" in r.json()["detail"].lower()
    r = client.patch(f"/admin/users/{username}/status", headers=h, json={"status": "active"})
    assert r.status_code == 200 and r.json()["status"] == "active"
    assert client.post("/login", json={"username": username, "password": "secret123"}).status_code == 200


def test_admin_direct_provision_and_password_reset():
    username = _unique_user("rohan")
    _track(username)
    h = auth_headers("supervisor")
    r = client.post("/admin/users", headers=h, json={
        "username": username, "password": "firstpass1", "role": "investigator",
        "name": "ACP Rohan", "badge_id": "DL-9001", "department": "Special Crime Branch",
    })
    assert r.status_code == 201, r.text
    r = client.post(f"/admin/users/{username}/reset-password", headers=h, json={"new_password": "rotated99"})
    assert r.status_code == 200 and r.json()["temporary_password"] == "rotated99"
    assert client.post("/login", json={"username": username, "password": "firstpass1"}).status_code == 401
    assert client.post("/login", json={"username": username, "password": "rotated99"}).status_code == 200


def test_admin_list_and_audit_trail():
    h = auth_headers("supervisor")
    r = client.get("/admin/users", headers=h)
    assert r.status_code == 200 and r.json()["count"] >= 3
    r = client.get("/admin/users?status=active", headers=h)
    assert r.status_code == 200
    assert all(u["status"] == "active" for u in r.json()["users"])
    r = client.get("/admin/audit-trail?limit=5", headers=h)
    assert r.status_code == 200 and "events" in r.json()


def test_admin_endpoints_gated_to_supervisor():
    for role in ("analyst", "investigator"):
        h = auth_headers(role)
        assert client.get("/admin/users", headers=h).status_code == 403
        assert client.post("/admin/users", headers=h, json={
            "username": _unique_user("x"), "password": "secret123", "role": "analyst",
        }).status_code == 403
        assert client.get("/admin/audit-trail", headers=h).status_code == 403
    assert client.get("/admin/users").status_code == 401


def test_register_deprecated_returns_pending_request():
    username = _unique_user("legacy")
    _track(username)
    r = client.post("/register", json={"username": username, "password": "secret123", "role": "investigator"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "pending_approval"
    assert "access_token" not in body
    assert client.post("/login", json={"username": username, "password": "secret123"}).status_code == 403


def test_request_access_validation():
    assert client.post("/auth/request-access", json={
        "username": _unique_user(), "password": "secret123", "role": "supervisor",
    }).status_code == 400
    assert client.post("/auth/request-access", json={
        "username": "ab", "password": "secret123", "role": "investigator",
    }).status_code == 400
    assert client.post("/auth/request-access", json={
        "username": _unique_user(), "password": "123", "role": "investigator",
    }).status_code == 400
    username = _unique_user("dup")
    _track(username)
    assert client.post("/auth/request-access", json={
        "username": username, "password": "secret123", "role": "investigator",
    }).status_code == 201
    r = client.post("/auth/request-access", json={
        "username": username, "password": "secret123", "role": "investigator",
    })
    assert r.status_code == 409


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
