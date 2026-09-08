"""
Tests for Pillar 1 (Facial Recognition & Search by Image), Pillar 2 (Templates Viewer & Downloader),
and Pillar 3 (Universal Ingestion & Bootstrapping Resilience).
"""
from pathlib import Path
from fastapi.testclient import TestClient
from backend.api.main import app
from backend.auth import create_token
from backend.analytics.face_search import search_face, get_face_index, extract_image_features, calculate_match_percentage
from backend.ingestion.normalizer import parse_amount, parse_date, normalize_with_quarantine
from backend.ingestion.mapper import clean_header, suggest_mapping, apply_mapping
from backend.ingestion.bootstrap import bootstrap_entities
from backend.ingestion.detector import sniff_delimiter

client = TestClient(app)
ROOT = Path(__file__).resolve().parent.parent

def test_facial_recognition_embeddings_and_search():
    index = get_face_index()
    assert len(index) >= 43, f"Expected at least 43 suspects indexed, found {len(index)}"
    assert "A1" in index
    assert index["A1"]["name"] == "Anwar Sheikh"
    
    # Test feature extraction on sample image
    img_path = ROOT / "data" / "mugshots" / "A1.jpg"
    assert img_path.exists()
    img_bytes = img_path.read_bytes()
    vec = extract_image_features(img_bytes)
    assert len(vec) == 128
    
    # Search with A1 image -> A1 should be top match with >= 95% confidence
    results = search_face(img_bytes, top_k=3)
    assert len(results) > 0
    top = results[0]
    assert top["id"] == "A1"
    assert top["similarity_score"] >= 95.0
    assert top["confidence_level"] == "High Probability Match"

def test_api_mugshots_and_search_image():
    # Test GET /mugshots/A1.jpg
    r = client.get("/mugshots/A1.jpg")
    assert r.status_code == 200
    assert "image" in r.headers["content-type"]
    
    # Test 404 on nonexistent mugshot
    r404 = client.get("/mugshots/nonexistent_xyz.jpg")
    assert r404.status_code == 404

    # Test POST /people/search-image without auth (should 401)
    img_path = ROOT / "data" / "mugshots" / "A1.jpg"
    with open(img_path, "rb") as f:
        r_noauth = client.post("/people/search-image", files={"file": ("query.jpg", f, "image/jpeg")})
    assert r_noauth.status_code == 401

    # Test with valid investigator auth token
    token = create_token({"username": "investigator", "role": "investigator", "name": "Investigator"})
    headers = {"Authorization": f"Bearer {token}"}
    with open(img_path, "rb") as f:
        r_auth = client.post("/people/search-image", files={"file": ("query.jpg", f, "image/jpeg")}, headers=headers)
    assert r_auth.status_code == 200
    data = r_auth.json()
    assert data["status"] == "success"
    assert len(data["matches"]) > 0
    assert data["matches"][0]["id"] == "A1"

def test_templates_endpoints():
    # GET /templates/schema
    r = client.get("/templates/schema")
    assert r.status_code == 200
    schemas = r.json()
    assert "cdrs" in schemas
    assert "transactions" in schemas
    assert "firs" in schemas
    assert "people_directory" in schemas

    # GET /templates/cdrs.csv
    r_csv = client.get("/templates/cdrs.csv")
    assert r_csv.status_code == 200
    assert "text/csv" in r_csv.headers["content-type"]
    assert "caller_phone" in r_csv.text

def test_currency_and_date_cleanser():
    assert parse_amount("₹45,000.00") == 45000
    assert parse_amount("$12,500") == 12500
    assert parse_amount("45000/-") == 45000
    assert parse_amount("2.5L") == 250000
    assert parse_amount("3.2 Cr") == 32000000
    assert parse_amount("1.5k") == 1500

    assert parse_date("2026-01-05 14:30:00") == "2026-01-05 14:30:00"
    assert "2026-01-05" in parse_date("05/01/2026 14:30:00")

def test_entity_bootstrapping():
    messy_datasets = {
        "cdrs": [
            {"caller_phone": "9999999999", "callee_phone": "8888888888", "caller_name": "New Suspect Alpha", "callee_name": "New Suspect Beta"}
        ],
        "transactions": [
            {"sender_account": "AC99999999", "receiver_account": "AC88888888", "sender_id": "UNK0", "receiver_id": "UNK1", "amount_inr": 100000}
        ]
    }
    empty_pd = {"network_people": [], "noise_people": []}
    booted_pd, stats = bootstrap_entities(messy_datasets, empty_pd)
    assert stats["phones_bootstrapped"] == 2
    assert len(booted_pd["network_people"]) >= 2

def test_synthetic_case_column_mapping():
    from backend.ingestion.mapper import suggest_mapping, validate_mapping
    # Test shorthand CDR columns
    cdr_cols = ["call_id", "caller", "receiver", "day", "duration_sec", "type"]
    m_cdr = suggest_mapping(cdr_cols, "cdrs")
    valid_cdr, missing_cdr = validate_mapping(m_cdr, "cdrs")
    assert valid_cdr is True, f"CDR mapping should be valid, missing: {missing_cdr}"
    assert m_cdr["caller_phone"] in ("caller", "call_id") or m_cdr["caller_id"] == "caller"

    # Test shorthand criminal history columns
    ch_cols = ["history_id", "person_id", "prior_cases", "category"]
    m_ch = suggest_mapping(ch_cols, "criminal_history")
    valid_ch, missing_ch = validate_mapping(m_ch, "criminal_history")
    assert valid_ch is True, f"Criminal history mapping should be valid, missing: {missing_ch}"

    # Test shorthand surveillance reports
    surv_cols = ["surveillance_id", "person_id", "day", "location", "text", "confidence"]
    m_surv = suggest_mapping(surv_cols, "surveillance_reports")
    valid_surv, missing_surv = validate_mapping(m_surv, "surveillance_reports")
    assert valid_surv is True, f"Surveillance mapping should be valid, missing: {missing_surv}"

def test_direct_upload_and_process_without_manual_mapping():
    from fastapi.testclient import TestClient
    from backend.api.main import app
    from backend.auth import create_token
    client = TestClient(app)
    token = create_token({"username": "investigator", "role": "investigator", "name": "Investigator"})
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Create investigation
    r_create = client.post("/investigations", json={"name": "Auto Ingest Case", "description": "Testing automated mapping"}, headers=headers)
    assert r_create.status_code == 200
    iid = r_create.json()["id"]

    try:
        # 2. Upload shorthand CSVs directly
        cdr_csv = "call_id,caller,receiver,day,duration_sec,type\nC001,9876543210,9123456780,1,120,voice\n"
        r_upload = client.post(
            f"/investigations/{iid}/upload",
            files={"files": ("cdrs.csv", cdr_csv.encode("utf-8"), "text/csv")},
            headers=headers
        )
        assert r_upload.status_code == 200

        # 3. Process directly without calling /mapping
        r_proc = client.post(f"/investigations/{iid}/process", headers=headers)
        assert r_proc.status_code == 200, f"Processing failed: {r_proc.text}"
        data = r_proc.json()
        assert data["stats"]["node_count"] >= 2
    finally:
        client.delete(f"/investigations/{iid}", headers=headers)


