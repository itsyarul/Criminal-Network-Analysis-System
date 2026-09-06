from fastapi.testclient import TestClient
from backend.api.main import app

from conftest import auth_headers

client = TestClient(app)
H = auth_headers("supervisor")

def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["graph_nodes"] > 0

def test_graph_day_snapshot():
    r = client.get("/graph?day=58", headers=H)
    assert r.status_code == 200
    data = r.json()
    assert "nodes" in data and "edges" in data
    assert data["day"] == 58
    # ghost trails 6-day fade
    assert len(data["edges"]) > 0

def test_graph_bad_day():
    r = client.get("/graph?day=999", headers=H)
    assert r.status_code == 422
    r = client.get("/graph?day=0", headers=H)
    assert r.status_code == 422

def test_bridges():
    r = client.get("/bridges", headers=H)
    assert r.status_code == 200
    data = r.json()
    assert len(data) > 0
    assert all("bridge_score" in b for b in data)

def test_bursts():
    r = client.get("/bursts", headers=H)
    assert r.status_code == 200
    assert len(r.json()) > 0

def test_why():
    r = client.get("/why/X1", headers=H)
    assert r.status_code == 200
    data = r.json()
    assert "top_signals" in data
    assert "sources" in data
    assert len(data["sources"]) >= 2
    assert "Potential investigative lead" in data["disclaimer"]

def test_why_404():
    r = client.get("/why/UNKNOWN123", headers=H)
    assert r.status_code == 404

def test_ask_bridges():
    r = client.get("/ask", params={"q": "Who connects Cell A and Cell B"}, headers=H)
    assert r.status_code == 200
    assert r.json()["template_id"] == 1

def test_ask_unknown():
    r = client.get("/ask", params={"q": "blabla unknown gibberish"}, headers=H)
    assert r.status_code == 400
    assert "templates" in r.json()["detail"]

def _whatif_iid():
    r = client.post("/investigations", headers=H, json={"name": "whatif-test"})
    assert r.status_code == 200
    iid = r.json()["id"]
    r = client.post(f"/investigations/{iid}/process", headers=H)
    assert r.status_code == 200, r.text
    return iid

def test_whatif_remove_node():
    iid = _whatif_iid()
    r = client.get(f"/investigations/{iid}/whatif", params={"remove_id": "X1"}, headers=H)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["remove_id"] == "X1"
    assert d["remaining_nodes"] == d["original_nodes"] - 1
    assert d["remaining_edges"] < d["original_edges"]
    assert d["removed_edges"] == d["original_edges"] - d["remaining_edges"]
    assert d["disconnected_components"] >= 1
    assert 0 <= d["impact_score"] <= 100
    assert d["simulation_only"] is True

def test_whatif_unknown_node():
    iid = _whatif_iid()
    r = client.get(f"/investigations/{iid}/whatif", params={"remove_id": "NOPE"}, headers=H)
    assert r.status_code == 404

def test_whatif_role_gated():
    from conftest import auth_headers
    iid = _whatif_iid()
    assert client.get(f"/investigations/{iid}/whatif", params={"remove_id": "X1"}).status_code == 401
    assert client.get(f"/investigations/{iid}/whatif", params={"remove_id": "X1"}, headers=auth_headers("analyst")).status_code == 403
    assert client.get(f"/investigations/{iid}/whatif", params={"remove_id": "X1"}, headers=auth_headers("investigator")).status_code == 200

def test_towers_schematic():
    from conftest import auth_headers
    assert client.get("/towers").status_code == 401
    assert client.get("/towers", headers=auth_headers("analyst")).status_code == 403
    r = client.get("/towers", headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] > 0 and len(body["towers"]) == body["count"]
    assert "Schematic" in body["disclaimer"]
    t0 = body["towers"][0]
    assert {"tower_id", "label", "call_count", "cells", "dominant_cell", "co_location_count"} <= set(t0)
    assert t0["tower_id"].startswith("TWR-")
    assert sum(t["call_count"] for t in body["towers"]) == sum(
        1 for e in client.get("/graph", headers=H).json()["edges"] if e["kind"] == "CALLED")
