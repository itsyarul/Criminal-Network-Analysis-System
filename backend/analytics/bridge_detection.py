"""
Bridge detection — 0.6*norm_betweenness + 0.4*(cross_cell_degree / max_cross_cell_degree)
Top-6 flagged, success = X1-X4 all in top-6 per design.
"""
from typing import Dict, List
from collections import defaultdict, Counter
import pickle
from pathlib import Path

try:
    import networkx as nx
    HAS_NX = True
except ImportError:
    HAS_NX = False

from backend.config import PROJECT_ROOT, BRIDGE_NORM_BETWEENNESS_WEIGHT, BRIDGE_CROSS_DEGREE_WEIGHT, BRIDGE_TOP_K
from backend.analytics.centrality import compute_centrality

BRIDGE_IDS_GT = {"X1","X2","X3","X4"}

def compute_bridges(graph_serial: Dict = None) -> List[Dict]:
    centrality = compute_centrality(graph_serial=graph_serial)
    if not centrality:
        return []
    # Exclude Noise isolates if cell is Noise
    centrality = [c for c in centrality if c.get("cell") != "Noise"]
    if not centrality:
        return []
    # Build id -> betweenness
    id_to_bet = {c["id"]: c.get("betweenness",0) for c in centrality}
    vals = list(id_to_bet.values())
    mn, mx = min(vals), max(vals)
    span = mx - mn if mx != mn else 1
    norm_bet = {k: (v - mn)/span for k, v in id_to_bet.items()}

    # Compute cross-cell degree dynamically
    cross_deg = {}
    id_to_cell = {c["id"]: c.get("cell", "Unknown") for c in centrality}
    id_to_role = {c["id"]: c.get("role", "") for c in centrality}
    
    pkl = PROJECT_ROOT / "output" / "graph.pkl"
    G_graph = None

    if graph_serial and HAS_NX:
        G_graph = nx.Graph()
        for e in graph_serial.get("edges", []):
            G_graph.add_edge(e["src"], e["dst"])
        for nid in G_graph.nodes:
            cell = id_to_cell.get(nid)
            neigh = list(G_graph.neighbors(nid))
            cross = sum(1 for nb in neigh if id_to_cell.get(nb) != cell and id_to_cell.get(nb) not in (None, "Noise"))
            cross_deg[nid] = cross
    elif pkl.exists() and HAS_NX:
        with open(pkl, "rb") as f:
            G_pkl = pickle.load(f)
        G_graph = nx.Graph()
        for u, v, data in G_pkl.edges(data=True):
            G_graph.add_edge(u, v)
        for nid in G_graph.nodes:
            cell = id_to_cell.get(nid)
            neigh = list(G_graph.neighbors(nid))
            cross = sum(1 for nb in neigh if id_to_cell.get(nb) != cell and id_to_cell.get(nb) not in (None,"Noise"))
            cross_deg[nid] = cross
    else:
        for nid in id_to_bet:
            cross_deg[nid] = 0

    max_cross = max(cross_deg.values()) if cross_deg else 1
    max_cross = max_cross if max_cross else 1

    bridges = []
    for nid, bet in id_to_bet.items():
        nb = norm_bet.get(nid,0)
        cd = cross_deg.get(nid,0)
        score = BRIDGE_NORM_BETWEENNESS_WEIGHT * nb + BRIDGE_CROSS_DEGREE_WEIGHT * (cd / max_cross)
        
        cells_conn = []
        if G_graph and G_graph.has_node(nid):
            neigh = list(G_graph.neighbors(nid))
            c_set = set(id_to_cell.get(n) for n in neigh if id_to_cell.get(n) not in (None, "Noise", "Phone", "Account"))
            cells_conn = sorted([c for c in c_set if c])

        bridges.append({
            "id": nid,
            "name": next((c["name"] for c in centrality if c["id"]==nid), nid),
            "role": id_to_role.get(nid),
            "cell": id_to_cell.get(nid),
            "cells": cells_conn,
            "betweenness": id_to_bet.get(nid,0),
            "norm_betweenness": round(nb,4),
            "cross_cell_degree": cd,
            "bridge_score": round(score,4),
            "is_ground_truth_bridge": nid in BRIDGE_IDS_GT
        })

    bridges.sort(key=lambda x: x["bridge_score"], reverse=True)
    # top K
    for i, b in enumerate(bridges):
        b["rank"] = i+1
        b["flagged"] = i < BRIDGE_TOP_K
    return bridges

def top_bridges(k=6) -> List[Dict]:
    return [b for b in compute_bridges() if b["flagged"]][:k]
