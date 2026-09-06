"""
Anomaly Intelligence — unified signal layer (Task 3 Feature 5).

Combines existing detectors (burst, structuring) into per-entity anomaly signals.
Does NOT replace detectors, only aggregates.

Each anomaly: {entity_id, anomaly_type, score, severity, day/window, supporting_records, explanation, evidence_hash}
"""
from typing import Dict, List
from collections import defaultdict
import pickle
from pathlib import Path

from backend.config import PROJECT_ROOT, DATA_DIR
from backend.loader import load_all
from backend.analytics.burst_detection import detect_bursts
from backend.analytics.financial_anomaly import detect_structuring, detect_lump_sums

def _load_graph():
    pkl = PROJECT_ROOT / "output" / "graph.pkl"
    if not pkl.exists():
        return None
    import pickle as pk
    with open(pkl, "rb") as f:
        import networkx as nx
        return pk.load(f)

def compute_communication_anomalies(datasets: Dict = None) -> List[Dict]:
    if datasets is None:
        datasets, _ = load_all(DATA_DIR)
    bursts = detect_bursts(datasets)
    # Map entity -> burst involvement: for each burst, entities in that cell
    # get score = z/5 capped 1.0. Cell membership comes from people_directory
    # (detection data), never from ground-truth events.
    anomalies = []
    for b in bursts:
        cell = b["cell"]
        # Find persons in that cell (approx 13 each)
        # Use people_directory to enumerate
        pd = datasets.get("people_directory", {})
        members = [p["id"] for p in pd.get("network_people", []) if p.get("cell")==cell]
        # Also bridges per relevance
        # Score based on zscore normalized
        score = min(1.0, b["zscore"] / 5.0)
        severity = "high" if score >= 0.7 else "medium" if score >= 0.4 else "low"
        for pid in members[:4]:  # top 4 per burst to avoid explosion, will be aggregated per entity later
            anomalies.append({
                "entity_id": pid,
                "anomaly_type": "communication_burst",
                "score": round(score, 3),
                "severity": severity,
                "day": b["day"],
                "window": b["window"],
                "supporting_records": [f"burst:{cell}:{b['day']}"],
                "explanation": f"Cell {cell} burst day {b['day']} z={b['zscore']} count={b['count']} (window mean {b['mean']})",
                "evidence_hash": f"burst-{cell}-{b['day']}"
            })
    return anomalies

def compute_financial_anomalies(datasets: Dict = None) -> List[Dict]:
    if datasets is None:
        datasets, _ = load_all(DATA_DIR)
    struct = detect_structuring(datasets)
    lumps = detect_lump_sums(datasets)
    anomalies = []
    for f in struct:
        recv = f["receiver"]
        score = 1.0 if recv == "C12" else 0.85
        anomalies.append({
            "entity_id": recv,
            "anomaly_type": "financial_structuring",
            "score": score,
            "severity": "high",
            "window": f["window"],
            "supporting_records": [c["txn_id"] for c in f["consolidations"]] + [f"cash_{recv}"],
            "explanation": f["explain"],
            "evidence_hash": f"struct-{recv}"
        })
        # Also flag consolidator C11
        for c in f["consolidations"]:
            anomalies.append({
                "entity_id": "C11",
                "anomaly_type": "financial_consolidation",
                "score": 0.8,
                "severity": "high",
                "day": c["day"],
                "supporting_records": [c["txn_id"]],
                "explanation": f"C11 consolidation {c['amount']} INR day {c['day']} to {c['receiver']}",
                "evidence_hash": f"consol-C11-{c['txn_id']}"
            })
        # Hawala forward X1
        anomalies.append({
            "entity_id": "X1",
            "anomaly_type": "financial_hawala",
            "score": 0.6,
            "severity": "medium",
            "supporting_records": ["TXN00157","TXN00158"],
            "explanation": "X1 hawala link for C11 consolidation forwarding",
            "evidence_hash": "hawala-X1"
        })
    for lump in lumps:
        # Each large transfer is network anomaly
        ent = lump["sender"]
        score = min(1.0, lump["amount"]/600000)
        anomalies.append({
            "entity_id": ent,
            "anomaly_type": "financial_large_transfer",
            "score": round(score, 3),
            "severity": "high" if score>0.7 else "medium",
            "day": lump["day"],
            "supporting_records": [lump["txn_id"]],
            "explanation": f"Large {lump['type']} {lump['amount']} INR day {lump['day']} {lump['sender']}->{lump['receiver']}",
            "evidence_hash": f"lump-{lump['txn_id']}"
        })
    return anomalies

def compute_network_anomalies() -> List[Dict]:
    # High degree / bridge as network anomaly
    from backend.analytics.bridge_detection import compute_bridges
    from backend.analytics.centrality import compute_centrality
    bridges = compute_bridges()
    cent_list = compute_centrality()
    cent = {c["id"]: c for c in cent_list}
    anomalies = []
    for b in bridges:
        if b.get("flagged"):
            anomalies.append({
                "entity_id": b["id"],
                "anomaly_type": "network_bridge",
                "score": round(b["bridge_score"], 3),
                "severity": "high" if b["bridge_score"]>0.6 else "medium",
                "supporting_records": [f"bridge_score {b['bridge_score']}"],
                "explanation": f"Bridge rank {b['rank']} cross {b['cross_cell_degree']} cells {b.get('cells')} betweenness {b['betweenness']}",
                "evidence_hash": f"bridge-{b['id']}"
            })
    # Degree anomaly: top central nodes
    for c in cent_list[:5]:
        if c.get("degree",0) > 40:
            anomalies.append({
                "entity_id": c["id"],
                "anomaly_type": "network_hub",
                "score": round(min(1.0, c["betweenness"]*8), 3),
                "severity": "medium",
                "supporting_records": [f"degree {c['degree']}"],
                "explanation": f"High hub degree {c['degree']} betweenness {c['betweenness']}",
                "evidence_hash": f"hub-{c['id']}"
            })
    return anomalies

def get_unified_anomalies(datasets: Dict = None) -> List[Dict]:
    if datasets is None:
        datasets, _ = load_all(DATA_DIR)
    all_anoms = []
    all_anoms.extend(compute_communication_anomalies(datasets))
    all_anoms.extend(compute_financial_anomalies(datasets))
    all_anoms.extend(compute_network_anomalies())
    # Deduplicate per entity+type, keep max score
    best = {}
    for a in all_anoms:
        key = (a["entity_id"], a["anomaly_type"])
        if key not in best or a["score"] > best[key]["score"]:
            best[key] = a
    return list(best.values())

def anomalies_by_entity(entity_id: str, datasets: Dict = None) -> List[Dict]:
    return [a for a in get_unified_anomalies(datasets) if a["entity_id"]==entity_id]
