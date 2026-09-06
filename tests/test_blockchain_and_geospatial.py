"""
Unit and Integration Tests for Blockchain Evidence Ledger and Geospatial Engine.
Theme: Blockchain & Cybersecurity + High-Performance Geospatial Intelligence.
"""
import pytest
from starlette.testclient import TestClient

from backend.api.main import app
from backend.analytics.blockchain_ledger import (
    build_blockchain_ledger,
    generate_chain_of_custody_certificate,
    verify_evidence_hash_in_ledger,
    compute_merkle_root,
    sha256_hash
)
from backend.analytics.geospatial import (
    get_cell_towers_geospatial,
    get_suspect_trajectories,
    get_co_location_hotspots,
    LOCATION_COORDINATES
)
from backend.auth import create_token


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_headers():
    token = create_token({"username": "admin", "role": "supervisor", "name": "Super Admin"})
    return {"Authorization": f"Bearer {token}"}


# =========================================================================
# 1. BLOCKCHAIN LEDGER TESTS
# =========================================================================

def test_merkle_root_computation():
    hashes = [
        sha256_hash("Record A"),
        sha256_hash("Record B"),
        sha256_hash("Record C"),
    ]
    root1 = compute_merkle_root(hashes)
    root2 = compute_merkle_root(hashes)
    assert len(root1) == 64
    assert root1 == root2

    # Different data gives different root
    hashes_alt = [hashes[0], hashes[1], sha256_hash("Record Modified")]
    root_alt = compute_merkle_root(hashes_alt)
    assert root_alt != root1


def test_build_blockchain_ledger():
    ledger = build_blockchain_ledger(case_name="Operation Syndicate Strike", officer="Inspector Sharma")
    assert ledger["status"] == "success"
    assert "LEDGER-NCRB-" in ledger["ledger_id"]
    assert ledger["total_blocks"] >= 5
    assert ledger["chain_integrity_verified"] is True
    assert len(ledger["master_merkle_root"]) == 64
    assert ledger["case_name"] == "Operation Syndicate Strike"
    assert ledger["investigating_officer"] == "Inspector Sharma"

    # Genesis block verification
    b0 = ledger["blocks"][0]
    assert b0["index"] == 0
    assert b0["category"] == "SYSTEM"
    assert b0["previous_hash"] == "0" * 64

    # Chaining verification: block[i].prev == block[i-1].hash
    for i in range(1, len(ledger["blocks"])):
        assert ledger["blocks"][i]["previous_hash"] == ledger["blocks"][i - 1]["block_hash"]


def test_chain_of_custody_certificate():
    ledger = build_blockchain_ledger(case_name="Test Case", officer="Inspector Rao")
    cert = generate_chain_of_custody_certificate(ledger, officer_name="Inspector Rao", station="Central Crime Branch")

    assert "CERT-BSA63-" in cert["certificate_id"]
    assert cert["master_merkle_root"] == ledger["master_merkle_root"]
    assert "Section 63 of Bharatiya Sakshya Adhiniyam" in cert["legal_declaration"]
    assert cert["certifying_officer"] == "Inspector Rao"
    assert cert["police_station"] == "Central Crime Branch"
    assert len(cert["verification_token"]) == 64


def test_verify_evidence_hash():
    ledger = build_blockchain_ledger(case_name="Test Verification Case")
    
    # 1. Test valid block hash
    target_block = ledger["blocks"][1]
    res1 = verify_evidence_hash_in_ledger(target_block["block_hash"], ledger)
    assert res1["verified"] is True
    assert res1["block_index"] == 1

    # 2. Test tampered hash
    res2 = verify_evidence_hash_in_ledger("0000000000000000000000000000000000000000000000000000000000000000", ledger)
    assert res2["verified"] is False
    assert "HASH MISMATCH" in res2["message"]


# =========================================================================
# 2. GEOSPATIAL INTELLIGENCE TESTS
# =========================================================================

def test_cell_tower_heatmap():
    tower_data = get_cell_towers_geospatial()
    assert tower_data["status"] == "success"
    assert "center" in tower_data
    assert len(tower_data["towers"]) > 0

    first_tower = tower_data["towers"][0]
    assert "tower_id" in first_tower
    assert "lat" in first_tower
    assert "lng" in first_tower
    assert first_tower["call_count"] >= 0
    assert isinstance(first_tower["unique_suspects"], list)


def test_suspect_trajectories():
    traj_data = get_suspect_trajectories()
    assert traj_data["status"] == "success"
    assert len(traj_data["trajectories"]) > 0

    first_suspect = traj_data["trajectories"][0]
    assert "person_id" in first_suspect
    assert "timeline_events" in first_suspect
    if first_suspect["timeline_events"]:
        wp = first_suspect["timeline_events"][0]
        assert "lat" in wp
        assert "lng" in wp
        assert "location_name" in wp
        assert "day" in wp


def test_meeting_hotspots():
    hotspot_data = get_co_location_hotspots()
    assert hotspot_data["status"] == "success"
    assert "hotspots" in hotspot_data
    assert isinstance(hotspot_data["hotspots"], list)
    for h in hotspot_data["hotspots"]:
        assert h["suspects_count"] >= 2
        assert "lat" in h
        assert "lng" in h
        assert "location_name" in h


# =========================================================================
# 3. FASTAPI ENDPOINT INTEGRATION TESTS
# =========================================================================

def test_api_blockchain_ledger(client, auth_headers):
    resp = client.get("/blockchain-ledger", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert "master_merkle_root" in data
    assert len(data["blocks"]) >= 5


def test_api_chain_of_custody_certificate(client, auth_headers):
    resp = client.get("/chain-of-custody-certificate", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "CERT-BSA63-" in data["certificate_id"]
    assert "Section 63 of Bharatiya Sakshya Adhiniyam" in data["legal_declaration"]


def test_api_verify_evidence_hash(client, auth_headers):
    ledger_resp = client.get("/blockchain-ledger", headers=auth_headers)
    block_hash = ledger_resp.json()["blocks"][1]["block_hash"]

    # Verify valid hash
    resp_valid = client.post("/verify-evidence-hash", json={"hash": block_hash}, headers=auth_headers)
    assert resp_valid.status_code == 200
    assert resp_valid.json()["verified"] is True

    # Verify invalid hash
    resp_invalid = client.post("/verify-evidence-hash", json={"hash": "deadbeef" * 8}, headers=auth_headers)
    assert resp_invalid.status_code == 200
    assert resp_invalid.json()["verified"] is False


def test_api_geospatial_endpoints(client, auth_headers):
    # Towers
    r1 = client.get("/geospatial/towers", headers=auth_headers)
    assert r1.status_code == 200
    assert len(r1.json()["towers"]) > 0

    # Trajectories
    r2 = client.get("/geospatial/trajectories", headers=auth_headers)
    assert r2.status_code == 200
    assert len(r2.json()["trajectories"]) > 0

    # Hotspots
    r3 = client.get("/geospatial/hotspots", headers=auth_headers)
    assert r3.status_code == 200
    assert "hotspots" in r3.json()
