"""Task 2: AI Entity + Relationship Intelligence — provenance, confidence, no GT leakage."""
import json
from backend.config import PROJECT_ROOT, DATA_DIR
from backend.loader import load_all
from backend.extraction.entity_extractor import extract_all
from backend.resolution.resolver import resolve_entities, _initial_aware_score
from backend.pipeline import run_pipeline


def test_no_ground_truth_in_graph():
    run_pipeline(clean=False)
    d = json.loads((PROJECT_ROOT / "output" / "graph.json").read_text())
    assert not any(n.get("kind") == "Event" for n in d["nodes"]), "Event nodes must not come from ground truth"
    assert not any(e.get("source_type") == "ground_truth" for e in d["edges"]), "ground_truth edges leaked into pipeline"


def test_relationship_provenance():
    run_pipeline(clean=False)
    d = json.loads((PROJECT_ROOT / "output" / "graph.json").read_text())
    for e in d["edges"]:
        assert e.get("source"), f"edge missing source: {e}"
        assert e.get("source_type"), f"edge missing source_type: {e}"
        assert e.get("confidence") is not None, f"edge missing confidence: {e}"
    # unstructured + MENTIONED_IN must carry supporting text + hash
    for kind in ("MENTIONED_IN", "MET", "ASSOCIATED_WITH", "CALLS"):
        subset = [e for e in d["edges"] if e["kind"] == kind]
        for e in subset:
            assert e.get("supporting_text"), f"{kind} edge missing supporting_text: {e.get('source')}"
            assert e.get("evidence_hash"), f"{kind} edge missing evidence_hash: {e.get('source')}"


def test_resolution_initials_and_confidence():
    # "R. Kumar" style initials must score high against "Rahul Kumar"
    assert _initial_aware_score("R. Kumar", "Rahul Kumar") >= 85
    assert _initial_aware_score("Rahul K.", "Rahul Kumar") >= 85
    assert _initial_aware_score("Anwar Sheikh", "Rajan Naik") == 0.0
    # full resolver still avoids blind merges
    datasets, _ = load_all(DATA_DIR)
    all_entities, _ = extract_all(datasets)
    struct = [e for e in all_entities if e.get("confidence", 0) >= 0.8]
    unstruct = [e for e in all_entities if e.get("confidence", 0) < 0.8]
    _, res_rows = resolve_entities(struct, unstruct, datasets["people_directory"], datasets=datasets)
    for r in res_rows:
        assert 0 <= float(r["confidence"]) <= 1.0
    # merged rows carry explainable breakdown
    merged = [r for r in res_rows if "fuzzy(" in r["method"] or "exact" in r["method"]]
    assert merged, "expected at least one merged entity"
    assert all("name_score" in r and "evidence_hash" in r for r in merged)
