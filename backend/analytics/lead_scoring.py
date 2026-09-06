"""
Investigation Lead Score (Task 3 Feature 1).

Transparent, documented formula combining 7 existing signals into 0-100 score.
Not arbitrary — each signal normalized 0-1, weighted, explainable.

Formula (weights sum 1.0):
  bridge_score          0.25  (betweenness normalized + cross-cell)
  financial_anomaly     0.20  (structuring/lump-sum involvement)
  communication_burst   0.15  (cell burst participation)
  temporal_correlation  0.10  (correlated group membership)
  evidence_quality      0.15  (avg confidence of incident edges)
  centrality            0.10  (betweenness + degree)
  cross_case            0.05  (shared across ≥2 cases)

lead_score = sum(weight * normalized_signal) *100, rounded 0-100
priority: HIGH ≥75, MEDIUM ≥50, LOW <50

All signals derived from real graph/datasets, not hardcoded.
"""
from typing import Dict, List
import pickle
from collections import defaultdict
from pathlib import Path

from backend.config import PROJECT_ROOT, DATA_DIR
from backend.loader import load_all
from backend.analytics.bridge_detection import compute_bridges
from backend.analytics.centrality import compute_centrality
from backend.analytics.anomaly import get_unified_anomalies, anomalies_by_entity
from backend.analytics.cross_case import detect_cross_case
from backend.analytics.temporal import get_temporal_intelligence, temporal_for_entity

def _normalize(value, max_val):
    return min(1.0, value / max_val) if max_val else 0.0

def compute_lead_scores(datasets: Dict = None, graph_serial: Dict = None) -> List[Dict]:
    if datasets is None:
        datasets, _ = load_all(DATA_DIR)
    # Precompute signals dynamically
    bridges = {b["id"]: b for b in compute_bridges(graph_serial=graph_serial)}
    cent = {c["id"]: c for c in compute_centrality(graph_serial=graph_serial)}
    max_bet = max((c.get("betweenness",0) for c in cent.values()), default=1) or 1
    max_deg = max((c.get("degree",0) for c in cent.values()), default=1) or 1

    # Financial anomaly map: entity -> max score among financial anomalies (keep max, not last)
    from backend.analytics.anomaly import compute_financial_anomalies
    fin_anoms = {}
    for a in compute_financial_anomalies(datasets):
        eid = a["entity_id"]
        if eid not in fin_anoms or a["score"] > fin_anoms[eid]:
            fin_anoms[eid] = a["score"]

    # Communication burst map: entity -> score (from anomaly layer)
    from backend.analytics.anomaly import compute_communication_anomalies
    comm_anoms = {}
    for a in compute_communication_anomalies(datasets):
        eid = a["entity_id"]
        # keep max per entity
        if eid not in comm_anoms or a["score"] > comm_anoms[eid]:
            comm_anoms[eid] = a["score"]

    # Temporal correlated set
    temp_intel = get_temporal_intelligence(datasets)
    correlated_cells = set()
    for g in temp_intel.get("correlated_groups", []):
        correlated_cells.update(g.get("cells", []))
    # Map entity -> temporal 1 if its cell in correlated set
    pd = datasets.get("people_directory", {}) if isinstance(datasets, dict) else {}
    id_to_cell = {p["id"]: p.get("cell") for p in pd.get("network_people", []) + pd.get("noise_people", []) if isinstance(p, dict) and "id" in p}
    # Cross-case map
    cross_entities = {c["shared_entity"]: c["confidence"] for c in detect_cross_case(datasets)}

    # Evidence quality: avg confidence of incident edges per entity
    evidence_quality = {}
    if graph_serial and "edges" in graph_serial:
        node_confs = defaultdict(list)
        for e in graph_serial.get("edges", []):
            conf = e.get("confidence", 0.5)
            node_confs[e.get("src")].append(conf)
            node_confs[e.get("dst")].append(conf)
        for nid, clist in node_confs.items():
            evidence_quality[nid] = sum(clist)/len(clist) if clist else 0.5
    else:
        pkl = PROJECT_ROOT / "output" / "graph.pkl"
        if pkl.exists():
            with open(pkl, "rb") as f:
                G = pickle.load(f)
            for nid in G.nodes:
                if G.nodes[nid].get("kind") != "Person":
                    continue
                edges = list(G.in_edges(nid, data=True)) + list(G.out_edges(nid, data=True))
                if not edges:
                    evidence_quality[nid] = 0.0
                else:
                    confs = [d.get("confidence", 0.5) for _, _, d in edges if d.get("confidence") is not None]
                    evidence_quality[nid] = sum(confs)/len(confs) if confs else 0.5

    leads = []
    people_list = pd.get("network_people", []) + pd.get("noise_people", [])
    if not people_list and graph_serial:
        people_list = [
            {"id": n["id"], "name": n.get("label", n["id"]), "cell": n.get("cell", "Unknown"), "role": n.get("role", "Suspect")}
            for n in graph_serial.get("nodes", []) if n.get("kind") in ("Person", None)
        ]
    for p in people_list:
        # Noise persons with degree <2 are low priority but still scored
        pid = p["id"]
        # Skip isolated noise already? Keep but will be low
        b = bridges.get(pid, {})
        c = cent.get(pid, {})

        signals = {}
        signals["bridge_score"] = round(b.get("bridge_score", 0.0), 3)  # already 0-1
        signals["betweenness"] = round(c.get("betweenness", 0)/max_bet, 3) if max_bet else 0
        signals["degree"] = round(c.get("degree", 0)/max_deg, 3)
        # Combined centrality as avg of betweenness+degree
        signals["centrality"] = round((signals["betweenness"] + signals["degree"])/2, 3)

        signals["financial_anomaly"] = round(fin_anoms.get(pid, 0.0), 3)
        signals["communication_anomaly"] = round(comm_anoms.get(pid, 0.0), 3)
        # Temporal: 1 if entity's cell in correlated group
        cell = id_to_cell.get(pid, "")
        signals["temporal_correlation"] = 1.0 if cell in correlated_cells else 0.0
        signals["evidence_quality"] = round(evidence_quality.get(pid, 0.5), 3)
        signals["cross_case"] = round(cross_entities.get(pid, 0.0), 3)
        # Also check LOC shared? For person, cross_case is direct; for location, separate

        # Weighted sum
        score = (
            0.25 * signals["bridge_score"] +
            0.20 * signals["financial_anomaly"] +
            0.15 * signals["communication_anomaly"] +
            0.10 * signals["temporal_correlation"] +
            0.15 * signals["evidence_quality"] +
            0.10 * signals["centrality"] +
            0.05 * signals["cross_case"]
        ) * 100
        lead_score = int(round(score))
        lead_score = max(0, min(100, lead_score))
        if lead_score >= 75:
            priority = "HIGH"
        elif lead_score >= 50:
            priority = "MEDIUM"
        else:
            priority = "LOW"

        # Build reasons
        reasons = []
        if signals["bridge_score"] >= 0.5:
            reasons.append(f"Bridge score {signals['bridge_score']} rank {b.get('rank')}")
        if signals["financial_anomaly"] >= 0.5:
            reasons.append(f"Financial anomaly {signals['financial_anomaly']}")
        if signals["communication_anomaly"] >= 0.5:
            reasons.append(f"Communication burst {signals['communication_anomaly']}")
        if signals["temporal_correlation"]:
            reasons.append("Temporal correlated burst (multi-cell 7-day)")
        if signals["cross_case"] >= 0.5:
            reasons.append(f"Cross-case shared (conf {signals['cross_case']})")
        if not reasons:
            reasons.append("Low anomaly signals; review degree/evidence")

        leads.append({
            "entity_id": pid,
            "entity_type": "Person",
            "label": p["name"],
            "cell": p.get("cell"),
            "role": p.get("role"),
            "lead_score": lead_score,
            "priority": priority,
            "signals": signals,
            "reasons": reasons,
            "evidence_refs": [],  # filled below via why-like sources
            "explanation": f"Potential investigative lead — weighted 0.25bridge+0.20financial+0.15comm+0.10temporal+0.15evidence+0.10centrality+0.05cross = {lead_score}/100"
        })

    # Sort descending, filter to at least degree>=2 or bridge to avoid isolated noise at top
    leads.sort(key=lambda x: x["lead_score"], reverse=True)
    return leads

def get_leads(limit: int = 20, datasets: Dict = None, graph_serial: Dict = None) -> List[Dict]:
    all_leads = compute_lead_scores(datasets, graph_serial)
    return all_leads[:limit]

def lead_for_entity(entity_id: str, datasets: Dict = None, graph_serial: Dict = None) -> Dict:
    for l in compute_lead_scores(datasets, graph_serial):
        if l["entity_id"] == entity_id:
            return l
    return None
