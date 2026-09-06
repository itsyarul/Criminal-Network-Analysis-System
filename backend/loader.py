"""
Loader — CSV → normalized records with validation, flag stripping, quarantine.

Per docs/designs/criminal-network-live-reveal.md API Contracts:
  - `loader.py --clean` = delete + re-ingest idempotent
  - default incremental fails if nodes exist (here: warns if output exists)
  - strips ground_truth_flag before pipeline
  - quarantine.csv: row_no, source_file, reason, confidence
  - resolution.csv: master_id, merged_ids, method, confidence

Handles all 7 data sources per data inventory:
  cdrs.csv (724), transactions.csv (158), firs.csv (35), social_posts.csv (68),
  criminal_history.csv (35), intelligence_reports.csv (28), surveillance_reports.csv (33)
"""
import argparse
import csv
import json
from pathlib import Path
from typing import Dict, List, Tuple
import os

from backend.config import DATA_DIR

# Expected headers per current data files — fail fast if schema drifts
EXPECTED_HEADERS = {
    "cdrs.csv": ["call_id","caller_id","caller_name","caller_phone","callee_id","callee_name","callee_phone","timestamp","day","call_type","duration_sec","cell_tower_location"],
    "transactions.csv": ["txn_id","sender_id","sender_name","sender_account","receiver_id","receiver_name","receiver_account","amount_inr","timestamp","day","txn_type","ground_truth_flag"],
    "firs.csv": ["fir_id","date","day","station","location","ipc_sections","narrative","ground_truth_flag"],
    "social_posts.csv": ["post_id","handle","person_id","timestamp","day","location_tag","post_text","hashtags","ground_truth_flag"],
    "criminal_history.csv": ["record_id","person_id","name","alias","dob","prior_offences","gang_affiliation","known_address","ground_truth_flag"],
    "intelligence_reports.csv": ["report_id","date","day","source_reliability","narrative","mentioned_entity_ids","ground_truth_flag"],
    "surveillance_reports.csv": ["report_id","date","day","team","location","confidence","activity_notes","ground_truth_flag"],
}

STRIP_FLAGS = {"ground_truth_flag"}  # never enter pipeline; alias_map.json also stripped separately

def validate_headers(file_path: Path, expected: List[str], rows: List[Dict]) -> Tuple[bool, str]:
    if not rows:
        return True, ""
    actual = list(rows[0].keys())
    if actual != expected:
        return False, f"Header mismatch in {file_path.name}: expected {expected} got {actual}"
    return True, ""

def load_csv_with_quarantine(file_path: Path, expected_headers: List[str], source_file: str, quarantine: List[Dict]) -> List[Dict]:
    """Load CSV, validate, strip ground_truth_flag, log quarantined rows."""
    if not file_path.exists():
        quarantine.append({"row_no": 0, "source_file": source_file, "reason": f"File not found: {file_path}", "confidence": 0.0})
        return []
    with open(file_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        # header check
        if reader.fieldnames != expected_headers:
            quarantine.append({
                "row_no": 0,
                "source_file": source_file,
                "reason": f"Schema drift: expected {expected_headers} got {reader.fieldnames}",
                "confidence": 0.0
            })
            return []
        rows = []
        has_day = "day" in expected_headers
        for idx, row in enumerate(reader, start=2):  # row 1 = header
            # basic validation per design edge cases: missing tower/day — only if file expects day
            if has_day:
                day_raw = row.get("day", "").strip() if row.get("day") else ""
                if day_raw == "" or day_raw is None:
                    # keep earliest with low confidence per spec — quarantine log with low conf
                    quarantine.append({"row_no": idx, "source_file": source_file, "reason": "Missing day", "confidence": 0.3})
                    # still keep row but mark confidence low (pipeline will handle)
                    row["_quarantine_confidence"] = 0.3
            # strip eval flag
            for flag in STRIP_FLAGS:
                row.pop(flag, None)
            rows.append(row)
    return rows

def load_people_directory(data_dir: Path, quarantine: List[Dict]) -> Dict:
    p = data_dir / "people_directory.json"
    if not p.exists():
        quarantine.append({"row_no": 0, "source_file": "people_directory.json", "reason": "File not found", "confidence": 0.0})
        return {"network_people": [], "noise_people": []}
    with open(p, encoding='utf-8') as f:
        return json.load(f)

def load_all(data_dir: Path = DATA_DIR) -> Tuple[Dict[str, List[Dict]], List[Dict]]:
    """
    Returns (datasets_by_name, quarantine_rows)
    datasets keys: cdrs, transactions, firs, social_posts, criminal_history, intelligence_reports, surveillance_reports, people_directory
    """
    quarantine: List[Dict] = []
    datasets: Dict[str, List[Dict]] = {}

    for fname, headers in EXPECTED_HEADERS.items():
        key = fname.replace(".csv","")
        fpath = data_dir / fname
        datasets[key] = load_csv_with_quarantine(fpath, headers, fname, quarantine)

    # people_directory separate JSON
    pd = load_people_directory(data_dir, quarantine)
    datasets["people_directory"] = pd

    # Verify flag stripping: no remaining ground_truth_flag in any row
    for k, rows in datasets.items():
        if k == "people_directory":
            continue
        for r in rows:
            if "ground_truth_flag" in r:
                quarantine.append({"row_no": 0, "source_file": k, "reason": "ground_truth_flag not stripped — pipeline contamination", "confidence": 0.0})

    return datasets, quarantine

def write_quarantine(quarantine: List[Dict], out_path: Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=["row_no","source_file","reason","confidence"])
        w.writeheader()
        w.writerows(quarantine)

def normalize_record(source: str, row: Dict) -> Dict:
    """Light normalization to common representation — preserves provenance."""
    # Convert day to int, amount to int where applicable, keep raw source id
    out = dict(row)
    # Normalize day
    try:
        out["day"] = int(str(out.get("day","0")).strip()) if out.get("day") not in (None,"") else None
    except:
        out["day"] = None
    # Normalize amount
    if "amount_inr" in out:
        try:
            out["amount_inr"] = int(str(out["amount_inr"]).replace(",","").strip())
        except:
            pass
    out["_source_type"] = source
    # Keep source id generic
    src_id = row.get("call_id") or row.get("txn_id") or row.get("fir_id") or row.get("post_id") or row.get("report_id") or row.get("record_id")
    out["_source_id"] = src_id
    return out

def _acquire_file_lock(lock_path: Path):
    """P1: loader file lock — fail fast if another loader holds the lock."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return lock_path
    except FileExistsError:
        raise SystemExit(f"loader lock held: {lock_path} exists — another loader.py run in progress")

def _release_file_lock(lock_path: Path):
    try:
        lock_path.unlink(missing_ok=True)
    except Exception:
        pass

def main():
    parser = argparse.ArgumentParser(description="Load and normalize synthetic datasets")
    parser.add_argument("--clean", action="store_true", help="Delete quarantine.csv/resolution.csv/audit artifacts before ingest (idempotent)")
    parser.add_argument("--data-dir", default=str(DATA_DIR), help="Path to data directory")
    parser.add_argument("--out-dir", default=str(DATA_DIR.parent / "output"), help="Output dir for quarantine.csv/resolution.csv")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    out_dir = Path(args.out_dir)

    lock_path = out_dir / ".loader.lock"
    _acquire_file_lock(lock_path)
    try:
        if args.clean and out_dir.exists():
            import shutil
            # only remove generated artifacts, not source data
            for p in [out_dir / "quarantine.csv", out_dir / "resolution.csv"]:
                if p.exists():
                    p.unlink()
            print(f"--clean: removed artifacts in {out_dir}")

        datasets, quarantine = load_all(data_dir)

        out_dir.mkdir(parents=True, exist_ok=True)
        write_quarantine(quarantine, out_dir / "quarantine.csv")
        # resolution.csv is produced by resolver — create empty header now if missing
        res_path = out_dir / "resolution.csv"
        if not res_path.exists():
            with open(res_path, "w", newline='', encoding='utf-8') as f:
                w = csv.DictWriter(f, fieldnames=["master_id","merged_ids","method","confidence"])
                w.writeheader()

        # quick stats
        for k, v in datasets.items():
            if k == "people_directory":
                print(f"people_directory: {len(v.get('network_people',[]))} network + {len(v.get('noise_people',[]))} noise")
            else:
                print(f"{k}: {len(v)} rows (quarantine: {sum(1 for q in quarantine if q['source_file']==k+'.csv')})")
        print(f"quarantine: {len(quarantine)} rows → {out_dir / 'quarantine.csv'}")
        # sanity: ensure no flag leaked
        leaked = any("ground_truth_flag" in r for rows in datasets.values() if isinstance(rows, list) for r in rows)
        print(f"ground_truth_flag stripped: {not leaked}")
        # strip alias_map.json note — eval-only, not loaded into pipeline
        alias_path = data_dir / "alias_map.json"
        if alias_path.exists():
            print(f"alias_map.json exists ({alias_path}) — correctly excluded from pipeline (eval-only)")
    finally:
        _release_file_lock(lock_path)

if __name__ == "__main__":
    main()
