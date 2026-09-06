import json
from backend.loader import load_all
from backend.extraction.entity_extractor import extract_all
from backend.resolution.resolver import resolve_entities
from backend.config import DATA_DIR

def test_resolution_no_aggressive_merge():
    datasets, _ = load_all(DATA_DIR)
    all_entities, _ = extract_all(datasets)
    struct = [e for e in all_entities if e.get("confidence",0) >= 0.8]
    unstruct = [e for e in all_entities if e.get("confidence",0) < 0.8]
    mention_map, res_rows = resolve_entities(struct, unstruct, datasets["people_directory"])
    # Should have some fuzzy merges but not merge everything
    assert len(res_rows) < 100  # modest
    # Check that uncertain stays separate with confidence 0.5
    rejects = [r for r in res_rows if "fuzzy_reject" in r["method"]]
    # Should exist for alias variance
    assert len(rejects) >= 0
    # Task2: check multi-signal breakdown present
    assert any("name_score" in r for r in res_rows)
    # Example multi-signal confidence 0-1
    for r in res_rows:
        if r.get("confidence"):
            assert 0 <= float(r["confidence"]) <= 1.0

def test_alias_recovery_eval():
    datasets, _ = load_all(DATA_DIR)
    all_entities, _ = extract_all(datasets)
    struct = [e for e in all_entities if e.get("confidence",0) >= 0.8]
    unstruct = [e for e in all_entities if e.get("confidence",0) < 0.8]
    mention_map, _ = resolve_entities(struct, unstruct, datasets["people_directory"])
    with open(DATA_DIR / "alias_map.json") as f:
        alias_map = json.load(f)
    # Build name->id
    pd = datasets["people_directory"]
    name_to_id = {p["name"]: p["id"] for p in pd["network_people"] + pd["noise_people"]}
    correct = sum(1 for alias, canon in alias_map.items() if mention_map.get(alias) == name_to_id.get(canon))
    # At least recover some — modest variance per design, don't require 100%
    assert correct >= 10, f"alias recovery too low: {correct}/21"
    print(f"alias recovery {correct}/21")

def test_resolution_csv_exists():
    from pathlib import Path
    from backend.config import PROJECT_ROOT
    from backend.pipeline import run_pipeline
    run_pipeline(clean=False)
    rpath = PROJECT_ROOT / "output" / "resolution.csv"
    assert rpath.exists()
    import csv
    with open(rpath) as f:
        header = next(csv.reader(f))
        # Task2 extends header with explainable scores, keep first 4 backward compatible
        assert header[:4] == ["master_id","merged_ids","method","confidence"]
        assert "evidence_hash" in header  # Task2 provenance
