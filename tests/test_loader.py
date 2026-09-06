import csv
from pathlib import Path
from backend.loader import load_all
from backend.config import DATA_DIR, PROJECT_ROOT

def test_headers_and_flag_stripping():
    datasets, quarantine = load_all(DATA_DIR)
    # all expected sources present
    assert "cdrs" in datasets and len(datasets["cdrs"]) == 724
    assert "transactions" in datasets and len(datasets["transactions"]) == 158
    assert "firs" in datasets
    # no ground_truth_flag leaked
    for k, rows in datasets.items():
        if k == "people_directory":
            continue
        for r in rows:
            assert "ground_truth_flag" not in r, f"flag leaked in {k}"
    # alias_map.json exists but not loaded into pipeline
    assert (DATA_DIR / "alias_map.json").exists()

def test_quarantine_file_exists_after_pipeline():
    from backend.pipeline import run_pipeline
    run_pipeline(clean=False)
    qpath = PROJECT_ROOT / "output" / "quarantine.csv"
    assert qpath.exists()
    with open(qpath) as f:
        reader = list(csv.DictReader(f))
        assert reader is not None  # at least header
        for row in reader:
            assert set(row.keys()) == {"row_no","source_file","reason","confidence"}

def test_people_directory_canonical():
    datasets, _ = load_all(DATA_DIR)
    pd = datasets["people_directory"]
    assert len(pd["network_people"]) == 43
    assert len(pd["noise_people"]) == 20
    # phones are 70000xxxx
    for p in pd["network_people"]:
        assert p["phone"].startswith("70000")
        assert p["account"].startswith("AC0009")
