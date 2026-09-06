import pytest
from backend.analytics.lead_scoring import compute_lead_scores, lead_for_entity, get_leads
from backend.analytics.anomaly import get_unified_anomalies, compute_communication_anomalies, compute_financial_anomalies
from backend.analytics.cross_case import detect_cross_case
from backend.analytics.temporal import get_temporal_intelligence
from backend.loader import load_all
from backend.config import DATA_DIR

def test_lead_score_normalization_and_priority():
    leads = compute_lead_scores()
    assert len(leads) > 20
    for l in leads:
        assert 0 <= l["lead_score"] <= 100
        assert l["priority"] in ("HIGH","MEDIUM","LOW")
        assert "signals" in l and all(k in l["signals"] for k in ["bridge_score","financial_anomaly","communication_anomaly","temporal_correlation","evidence_quality","centrality","cross_case"])
        assert "reasons" in l and len(l["reasons"]) >= 1
        # score = weighted sum *100, check priority thresholds
        if l["lead_score"] >= 75:
            assert l["priority"] == "HIGH"
        elif l["lead_score"] >= 50:
            assert l["priority"] == "MEDIUM"
        else:
            assert l["priority"] == "LOW"

def test_lead_score_sorted_descending():
    leads = get_leads(limit=10)
    scores = [l["lead_score"] for l in leads]
    assert scores == sorted(scores, reverse=True)

def test_bridge_contribution():
    leads = {l["entity_id"]: l for l in compute_lead_scores()}
    # Bridge entities should have bridge_score >0
    assert leads["X4"]["signals"]["bridge_score"] > 0.3  # X4 is flagged bridge rank4
    # Non-bridge leaf like A5 should have low bridge
    assert leads["A5"]["signals"]["bridge_score"] < 0.3

def test_financial_anomaly_contribution():
    leads = {l["entity_id"]: l for l in compute_lead_scores()}
    # C12 is structuring receiver → financial 1.0
    assert leads["C12"]["signals"]["financial_anomaly"] == 1.0
    # C11 consolidator 0.8
    assert leads["C11"]["signals"]["financial_anomaly"] == 0.8
    # Random noise should be 0
    assert leads["N1"]["signals"]["financial_anomaly"] == 0.0

def test_evidence_quality_signal():
    leads = {l["entity_id"]: l for l in compute_lead_scores()}
    # All persons have evidence_quality 0.5-1.0 (avg edge confidence)
    for l in leads.values():
        assert 0.5 <= l["signals"]["evidence_quality"] <= 1.0

def test_cross_case_detection():
    datasets, _ = load_all(DATA_DIR)
    cc = detect_cross_case(datasets)
    assert len(cc) >= 1  # at least one shared location/person
    for c in cc:
        assert len(c["cases"]) >= 2
        assert 0 <= c["confidence"] <= 1.0
        assert "shared_entity" in c and "supporting_evidence" in c
        assert len(c["supporting_evidence"]) >= 2
        assert "Potential cross-case" in c["explanation"]
    # Check X1 is NOT necessarily shared across FIRs via person (X1 appears via transactions not FIR), but location shared should exist
    loc_shared = [c for c in cc if c["entity_type"]=="Location"]
    assert len(loc_shared) >= 1

def test_temporal_correlation_derived():
    intel = get_temporal_intelligence()
    assert "correlated_groups" in intel
    assert len(intel["correlated_groups"]) >= 1
    for g in intel["correlated_groups"]:
        assert len(g["cells"]) >= 2
        assert g["span"][1] - g["span"][0] <= 7
        assert "explanation" in g and "z>2.0" in g["explanation"]
    # Story slice 50-70 must contain bursts
    assert len(intel["story_slice"]["bursts_in_slice"]) >= 3
    # Check narrative Days 58/61/64 are derived, not hardcoded to exact
    for n in intel["narrative_days"]:
        assert n["target_day"] in (58,61,64)
        # If burst exists near target, offset <=3
        if n["actual_burst"]:
            assert abs(n["actual_burst"]["day"] - n["target_day"]) <= 3

def test_anomaly_unified_layer():
    anoms = get_unified_anomalies()
    assert len(anoms) >= 10
    for a in anoms:
        assert "entity_id" in a and "anomaly_type" in a and "score" in a and "severity" in a and "explanation" in a
        assert 0 <= a["score"] <= 1.0
        assert a["severity"] in ("high","medium","low")
        assert len(a["supporting_records"]) >= 1

def test_communication_and_financial_anomalies_real():
    fin = compute_financial_anomalies()
    # Must flag C12 structuring from real transactions.csv (22 cash <50k)
    assert any(a["entity_id"]=="C12" and a["anomaly_type"]=="financial_structuring" for a in fin)
    comm = compute_communication_anomalies()
    assert any(a["anomaly_type"]=="communication_burst" for a in comm)

def test_deterministic_lead_scores():
    l1 = compute_lead_scores()
    l2 = compute_lead_scores()
    assert [x["lead_score"] for x in l1] == [x["lead_score"] for x in l2]
    assert [x["entity_id"] for x in l1] == [x["entity_id"] for x in l2]

def test_leads_api_ranking():
    from fastapi.testclient import TestClient
    from backend.api.main import app
    from conftest import auth_headers
    client = TestClient(app)
    r = client.get("/leads?limit=5", headers=auth_headers())
    assert r.status_code == 200
    data = r.json()
    assert "leads" in data and len(data["leads"]) == 5
    assert data["leads"][0]["lead_score"] >= data["leads"][-1]["lead_score"]
    assert "formula" in data

def test_cross_case_api():
    from fastapi.testclient import TestClient
    from backend.api.main import app
    from conftest import auth_headers
    client = TestClient(app)
    r = client.get("/cross-case", headers=auth_headers())
    assert r.status_code == 200
    assert isinstance(r.json(), list)

def test_temporal_api():
    from fastapi.testclient import TestClient
    from backend.api.main import app
    from conftest import auth_headers
    client = TestClient(app)
    r = client.get("/temporal", headers=auth_headers())
    assert r.status_code == 200
    assert "correlated_groups" in r.json()
