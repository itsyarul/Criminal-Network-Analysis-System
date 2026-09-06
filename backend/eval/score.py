"""
Eval gate — scores pipeline exports against ground truth (eval-only).

Usage:
  python3 -m backend.eval.score            # human-readable table, exit 0 unless FAIL
  python3 -m backend.eval.score --json     # machine-readable JSON on stdout
  python3 -m backend.eval.score --strict   # exit 1 on PARTIAL too (CI gate)

Eval hygiene: this script reads ONLY exports (output/graph.json,
output/resolution.csv) plus eval-only inputs (data/ground_truth_network.json,
data/alias_map.json). It never touches Neo4j and never feeds ground truth
back into the pipeline. Target event days are derived from the ground-truth
events file, not hardcoded.

Checks (PASS / PARTIAL / FAIL):
  1. flag_hygiene    — no `ground_truth_flag` leaked into exports
  2. communities     — bridge-filtered Louvain/LPA recovers dominant A, B, C
  3. bridges         — X1-X4 all in top-6 (PARTIAL if >=1, FAIL if 0)
  4. bursts          — detected bursts within ±2 days of every GT event day
  5. structuring     — C12 flagged (>=10 small cash, >=2 consolidations)
  6. alias_recovery  — alias_map variants resolved in resolution.csv
                       (PARTIAL if >=50%, FAIL below)
"""
import argparse
import csv
import json
import sys
from pathlib import Path

from backend.config import DATA_DIR, PROJECT_ROOT
from backend.loader import load_all
from backend.graph.builder import load_graph_serial
from backend.analytics.burst_detection import detect_bursts
from backend.analytics.financial_anomaly import detect_structuring
from backend.analytics.bridge_detection import compute_bridges, BRIDGE_IDS_GT
from backend.analytics.community import detect_communities

OUTPUT_DIR = PROJECT_ROOT / "output"
TRUE_BRIDGES = sorted(BRIDGE_IDS_GT)


def check_flag_hygiene() -> dict:
    """No ground_truth_flag text anywhere in the exported artifacts."""
    leaked = []
    graph_json = OUTPUT_DIR / "graph.json"
    if graph_json.exists() and "ground_truth_flag" in graph_json.read_text():
        leaked.append("output/graph.json")
    res_csv = OUTPUT_DIR / "resolution.csv"
    if res_csv.exists() and "ground_truth_flag" in res_csv.read_text():
        leaked.append("output/resolution.csv")
    status = "FAIL" if leaked else "PASS"
    return {"status": status, "detail": f"leaked in {leaked}" if leaked else "flag absent from all exports"}


def check_communities() -> dict:
    comms = detect_communities(filter_bridges=True)
    dominants = {c.get("dominant_cell") for c in comms}
    missing = {"A", "B", "C"} - dominants
    status = "PASS" if not missing else "FAIL"
    return {
        "status": status,
        "detail": f"{len(comms)} communities, dominants={sorted(dominants)}"
        + (f", missing={sorted(missing)}" if missing else ""),
    }


def check_bridges() -> dict:
    bridges = compute_bridges()
    flagged = {b["id"] for b in bridges if b.get("flagged")}
    found = sorted(set(TRUE_BRIDGES) & flagged)
    missing = sorted(set(TRUE_BRIDGES) - flagged)
    ranks = {b["id"]: b["rank"] for b in bridges if b["id"] in TRUE_BRIDGES}
    if not missing:
        status = "PASS"
    elif found:
        status = "PARTIAL"
    else:
        status = "FAIL"
    return {
        "status": status,
        "detail": f"{len(found)}/4 true bridges in top-6: {found} ranks={ranks}"
        + (f" missing={missing}" if missing else ""),
    }


def check_bursts(datasets: dict, event_days: list) -> dict:
    bursts = detect_bursts(datasets)
    per_event = {}
    for day in event_days:
        nearby = sorted({b["day"] for b in bursts if abs(b["day"] - day) <= 2})
        per_event[str(day)] = nearby
    missed = [d for d, near in per_event.items() if not near]
    correlated = sum(1 for b in bursts if b.get("correlated"))
    if not missed:
        status = "PASS"
    elif len(missed) < len(per_event):
        status = "PARTIAL"
    else:
        status = "FAIL"
    return {
        "status": status,
        "detail": f"{len(bursts)} bursts ({correlated} correlated), coverage={per_event}"
        + (f" missed={missed}" if missed else ""),
    }


def check_structuring(datasets: dict) -> dict:
    flags = detect_structuring(datasets)
    c12 = next((f for f in flags if f["receiver"] == "C12"), None)
    if c12 and c12.get("cash_small_count", 0) >= 10 and len(c12.get("consolidations", [])) >= 2:
        return {
            "status": "PASS",
            "detail": f"C12 flagged: {c12['cash_small_count']} small cash, "
            f"{len(c12['consolidations'])} consolidations",
        }
    if c12:
        return {"status": "PARTIAL", "detail": f"C12 flagged but below thresholds: {c12.get('explain', '')}"}
    return {"status": "FAIL", "detail": f"C12 not flagged ({len(flags)} other flags)"}


def check_alias_recovery(datasets: dict) -> dict:
    alias_path = DATA_DIR / "alias_map.json"
    res_path = OUTPUT_DIR / "resolution.csv"
    if not alias_path.exists():
        return {"status": "FAIL", "detail": "data/alias_map.json missing"}
    if not res_path.exists():
        return {"status": "FAIL", "detail": "output/resolution.csv missing — run pipeline first"}
    alias_map = json.loads(alias_path.read_text())
    pd = datasets.get("people_directory", {})
    name_to_id = {p["name"]: p["id"] for p in pd.get("network_people", []) + pd.get("noise_people", [])}
    # master_id -> set of merged mention strings
    merged: dict = {}
    with open(res_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            merged.setdefault(row.get("master_id", ""), set()).add(row.get("merged_ids", ""))
    recovered = sum(
        1
        for alias, canon in alias_map.items()
        if any(alias in m for m in merged.get(name_to_id.get(canon, ""), set()))
    )
    total = len(alias_map)
    ratio = recovered / total if total else 0
    if ratio >= 1.0:
        status = "PASS"
    elif ratio >= 0.5:
        status = "PARTIAL"
    else:
        status = "FAIL"
    return {"status": status, "detail": f"{recovered}/{total} aliases resolved ({ratio:.0%})"}


def run_all() -> dict:
    datasets, _ = load_all(DATA_DIR)
    serial = load_graph_serial()
    if not serial["nodes"]:
        return {"error": "Graph not built — run: python3 -m backend.pipeline --clean first", "checks": {}}
    gt = json.loads((DATA_DIR / "ground_truth_network.json").read_text())
    event_days = sorted({e["day"] for e in gt.get("events", []) if isinstance(e.get("day"), int)})
    checks = {
        "flag_hygiene": check_flag_hygiene(),
        "communities": check_communities(),
        "bridges": check_bridges(),
        "bursts": check_bursts(datasets, event_days),
        "structuring": check_structuring(datasets),
        "alias_recovery": check_alias_recovery(datasets),
    }
    statuses = [c["status"] for c in checks.values()]
    overall = "FAIL" if "FAIL" in statuses else ("PARTIAL" if "PARTIAL" in statuses else "PASS")
    return {
        "overall": overall,
        "event_days": event_days,
        "graph": serial["stats"],
        "checks": checks,
        "disclaimer": "Investigative-lead scoring aid — not a guilt determination.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Eval gate: score pipeline exports vs ground truth")
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON on stdout")
    parser.add_argument("--strict", action="store_true", help="Exit 1 on PARTIAL as well as FAIL")
    args = parser.parse_args()

    report = run_all()
    if args.json:
        print(json.dumps(report, indent=2))
    elif "error" in report:
        print(f"ERROR: {report['error']}")
    else:
        print(f"=== Eval gate (events {report['event_days']}, "
              f"graph {report['graph']['node_count']}n/{report['graph']['edge_count']}e) ===")
        for name, c in report["checks"].items():
            mark = {"PASS": "✓", "PARTIAL": "~", "FAIL": "✗"}[c["status"]]
            print(f"  [{mark}] {c['status']:7} {name:14} {c['detail']}")
        print(f"Overall: {report['overall']}")

    if "error" in report:
        return 2
    if report["overall"] == "FAIL" or (args.strict and report["overall"] == "PARTIAL"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
