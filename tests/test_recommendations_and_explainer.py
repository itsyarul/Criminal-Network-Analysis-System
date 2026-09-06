"""
Automated test suite for:
1. "Explain this Connection" AI feature
2. Case Recommendation Engine ("Who Else to Check Out")
"""
import pytest
from fastapi.testclient import TestClient
from backend.api.main import app
from backend.auth import create_token
from backend.analytics.connection_explainer import explain_connection
from backend.analytics.case_recommender import generate_case_recommendations
from backend.loader import load_all
from backend.config import DATA_DIR
from backend.graph.builder import load_graph_serial

client = TestClient(app)

def test_connection_explainer_direct_pair():
    datasets, _ = load_all(DATA_DIR)
    graph = load_graph_serial()
    
    # Test A1 <-> A2 (both in Cell A, frequent callers)
    res = explain_connection("A1", "A2", datasets=datasets, graph=graph)
    assert res["status"] == "success"
    assert res["source_person"]["id"] == "A1"
    assert res["target_person"]["id"] == "A2"
    assert len(res["story_synopsis"]) > 50
    assert "Telephony Evidence" in res["story_synopsis"] or "Direct Coordinated" in res["relationship_strength"] or "Direct" in res["relationship_strength"]
    assert res["telephony"]["total_calls"] > 0
    assert res["evidence_score"] >= 1

def test_connection_explainer_bridge_pair():
    datasets, _ = load_all(DATA_DIR)
    graph = load_graph_serial()
    
    # Test A1 <-> X1 (Bridge connection)
    res = explain_connection("A1", "X1", datasets=datasets, graph=graph)
    assert res["status"] == "success"
    assert "X1" in res["target_person"]["id"] or "A1" in res["source_person"]["id"]
    assert len(res["story_synopsis"]) > 50

def test_api_explain_connection():
    token = create_token({"username": "investigator", "role": "investigator", "name": "Investigator"})
    headers = {"Authorization": f"Bearer {token}"}
    
    # Global endpoint
    r = client.get("/connections/explain?src=A1&dst=A2", headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "success"
    assert data["source_person"]["id"] == "A1"
    assert "story_synopsis" in data
    assert "financials" in data
    assert "telephony" in data
    assert "police_cases" in data

def test_case_recommendation_engine():
    datasets, _ = load_all(DATA_DIR)
    graph = load_graph_serial()
    
    res = generate_case_recommendations(datasets=datasets, graph=graph, limit=10)
    assert res["status"] == "success"
    assert res["total_recommendations"] > 0
    assert len(res["recommendations"]) > 0
    
    # Check shape of recommendations
    first = res["recommendations"][0]
    assert "id" in first
    assert "title" in first
    assert "reason" in first
    assert "priority" in first
    assert "supporting_signals" in first
    assert "action_type" in first

def test_api_case_recommendations():
    token = create_token({"username": "investigator", "role": "investigator", "name": "Investigator"})
    headers = {"Authorization": f"Bearer {token}"}
    
    r = client.get("/recommendations?limit=8", headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "success"
    assert len(data["recommendations"]) > 0
    assert "proactive_summary" in data

    # Suspect specific recommendations
    r_pid = client.get("/people/A1/recommendations?limit=5", headers=headers)
    assert r_pid.status_code == 200
    data_pid = r_pid.json()
    assert data_pid["status"] == "success"
