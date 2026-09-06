"""
Unit and Integration Tests for Tactical Takedown & Arrest Optimization Simulator.
Theme: Command-Level Syndicate Dismantlement & Multi-Target Raid Optimization Engine.
"""
import pytest
from starlette.testclient import TestClient

from backend.api.main import app
from backend.analytics.takedown_simulator import (
    _build_adj_list,
    _calculate_connected_components,
    _calculate_network_efficiency,
    _get_person_priors,
    simulate_takedown,
    get_takedown_strategies,
    generate_operation_order,
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
# 1. GRAPH METRICS & HELPER UNIT TESTS
# =========================================================================

def test_network_efficiency_isolated():
    # Empty or single-node network efficiency should be 0.0
    adj_single = {"A": set()}
    assert _calculate_network_efficiency(adj_single) == 0.0

    # Two connected nodes: d(A,B)=1, d(B,A)=1 -> sum = 2 -> eff = 2 / (2 * 1) = 1.0
    adj_two = {"A": {"B"}, "B": {"A"}}
    assert round(_calculate_network_efficiency(adj_two), 2) == 1.0

    # Linear 3 nodes: A-B-C -> d(A,B)=1, d(A,C)=2, d(B,C)=1
    # total_inv = 1 + 0.5 + 1 + 1 + 0.5 + 1 = 5.0 -> eff = 5.0 / (3 * 2) = 0.8333
    adj_three = {"A": {"B"}, "B": {"A", "C"}, "C": {"B"}}
    assert round(_calculate_network_efficiency(adj_three), 3) == 0.833


def test_connected_components_calculation():
    adj = {
        "A": {"B"},
        "B": {"A"},
        "C": {"D"},
        "D": {"C"},
        "E": set(),
    }
    comps = _calculate_connected_components(adj)
    assert len(comps) == 3
    lens = sorted([len(c) for c in comps])
    assert lens == [1, 2, 2]


def test_build_adj_list_omission():
    nodes = [{"id": "P01"}, {"id": "P02"}, {"id": "P03"}]
    edges = [{"src": "P01", "dst": "P02"}, {"src": "P02", "dst": "P03"}]
    
    # Exclude P02
    adj = _build_adj_list(nodes, edges, excluded_nodes={"P02"})
    assert "P02" not in adj
    assert "P01" in adj and len(adj["P01"]) == 0
    assert "P03" in adj and len(adj["P03"]) == 0


def test_get_person_priors():
    mock_datasets = {
        "firs": [
            {"suspects": "P01, P02", "sections_applied": "IPC 302, Arms Act 25", "description": "Gun violence murder case"}
        ],
        "criminal_history": [
            {"person_id": "P01", "prior_offences": "Arms possession, extortion"}
        ]
    }
    prior_p01 = _get_person_priors("P01", mock_datasets)
    assert prior_p01["weapon_involved"] is True
    assert "CRITICAL" in prior_p01["risk_level"]
    assert len(prior_p01["violent_charges"]) > 0

    prior_clean = _get_person_priors("P99", mock_datasets)
    assert prior_clean["weapon_involved"] is False
    assert prior_clean["risk_level"] == "STANDARD"


# =========================================================================
# 2. SIMULATION & STRATEGY ENGINE TESTS
# =========================================================================

def test_simulate_takedown_basic():
    res = simulate_takedown(target_ids=["P01", "P03"])
    assert res["status"] == "success"
    assert res["targets_count"] == 2
    assert 0.0 <= res["dismantlement_score_pct"] <= 100.0
    assert res["isolated_fragments_count"] >= 1
    assert res["severed_channels_count"] >= 0
    assert res["recoverable_assets_inr"] >= 0.0
    assert len(res["target_profiles"]) == 2
    
    alloc = res["tactical_resource_allocation"]
    assert alloc["armed_tactical_units"] >= 1
    assert alloc["cyber_forensics_officers"] >= 1
    assert alloc["perimeter_containment_squads"] >= 1
    assert alloc["total_personnel_required"] > 0


def test_get_takedown_strategies():
    strats = get_takedown_strategies()
    assert strats["status"] == "success"
    assert strats["total_strategies"] == 4
    
    ids = [s["id"] for s in strats["strategies"]]
    assert "strategy_sync" in ids
    assert "strategy_bridge" in ids
    assert "strategy_decap" in ids
    assert "strategy_mule" in ids

    # Check that each strategy has valid metrics
    for s in strats["strategies"]:
        assert len(s["target_ids"]) > 0
        assert "dismantlement_score_pct" in s["metrics"]
        assert s["metrics"]["dismantlement_score_pct"] >= 0.0

    assert len(strats["available_suspects"]) > 0


def test_generate_operation_order():
    op = generate_operation_order(
        strategy_id_or_targets="strategy_sync",
        commander_name="DCP Vikram Rathore",
        codename="OPERATION IRON FIST"
    )
    assert "OPORD-NCRB-OPERATION_IRON_FIST" in op["operation_order_id"]
    assert op["operation_codename"] == "OPERATION IRON FIST"
    assert op["commanding_officer"] == "DCP Vikram Rathore"
    assert op["security_classification"] == "TOP SECRET // LAW ENFORCEMENT SENSITIVE"
    assert len(op["target_manifest"]) > 0
    assert len(op["legal_and_procedural_directives"]) >= 4
    assert "SECTION 63 BSA 2023" in op["legal_and_procedural_directives"][0].upper()

    # Test with direct target list
    op_custom = generate_operation_order(["P01", "P02", "P03"], codename="OPERATION COBRA")
    assert op_custom["operation_codename"] == "OPERATION COBRA"
    assert len(op_custom["target_manifest"]) == 3


# =========================================================================
# 3. FASTAPI REST API INTEGRATION TESTS
# =========================================================================

def test_api_get_takedown_strategies(client, auth_headers):
    resp = client.get("/takedown/strategies", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert len(data["strategies"]) == 4


def test_api_post_takedown_simulate(client, auth_headers):
    payload = {
        "target_ids": ["P01", "P03", "P05"],
        "freeze_financial_accounts": True
    }
    resp = client.post("/takedown/simulate", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["targets_count"] == 3
    assert data["dismantlement_score_pct"] >= 0.0


def test_api_post_operation_order(client, auth_headers):
    payload = {
        "target_ids": ["P01", "P02"],
        "strategy_id": "strategy_sync",
        "commander_name": "ACP Ananya Roy",
        "codename": "OPERATION THUNDER"
    }
    resp = client.post("/takedown/operation-order", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["operation_codename"] == "OPERATION THUNDER"
    assert data["commanding_officer"] == "ACP Ananya Roy"
    assert "target_manifest" in data
    assert len(data["target_manifest"]) > 0


def test_api_investigation_scoped_endpoints(client, auth_headers):
    # Test investigation scoped strategies
    r1 = client.get("/investigations/inv-default/takedown/strategies", headers=auth_headers)
    assert r1.status_code == 200
    assert len(r1.json()["strategies"]) == 4

    # Test investigation scoped simulate
    r2 = client.post("/investigations/inv-default/takedown/simulate", json={"target_ids": ["P01"]}, headers=auth_headers)
    assert r2.status_code == 200
    assert r2.json()["targets_count"] == 1

    # Test investigation scoped operation-order
    r3 = client.post("/investigations/inv-default/takedown/operation-order", json={"strategy_id": "strategy_bridge"}, headers=auth_headers)
    assert r3.status_code == 200
    assert "OPORD-NCRB" in r3.json()["operation_order_id"]


def test_api_unauthenticated_access_blocked(client):
    # Endpoints require authentication
    r1 = client.get("/takedown/strategies")
    assert r1.status_code in (401, 403)

    r2 = client.post("/takedown/simulate", json={"target_ids": ["P01"]})
    assert r2.status_code in (401, 403)

    r3 = client.post("/takedown/operation-order", json={"strategy_id": "strategy_sync"})
    assert r3.status_code in (401, 403)
