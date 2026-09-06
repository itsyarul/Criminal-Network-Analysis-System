"""
Centrality / Betweenness — uses Neo4j GDS if available else networkx.
Per design: betweenness ranks X1-X4 top-6; we compute normalized 0-1.
"""
from typing import Dict, List
import pickle
from pathlib import Path

try:
    import networkx as nx
    HAS_NX = True
except ImportError:
    HAS_NX = False

from backend.config import PROJECT_ROOT, BRIDGE_TOP_K
from backend.graph.neo4j_client import get_driver, is_available
from backend.config import NEO4J_DATABASE

def compute_centrality_networkx(graph_serial: Dict = None, pkl_path: Path = None) -> List[Dict]:
    if graph_serial and HAS_NX:
        G = nx.MultiDiGraph()
        for n in graph_serial.get("nodes", []):
            G.add_node(n["id"], label=n.get("label", n["id"]), kind=n.get("kind", "Person"), cell=n.get("cell"), role=n.get("role"))
        for e in graph_serial.get("edges", []):
            G.add_edge(e["src"], e["dst"], kind=e.get("kind"))
    else:
        pkl = pkl_path or (PROJECT_ROOT / "output" / "graph.pkl")
        if not pkl.exists() or not HAS_NX:
            return []
        with open(pkl, "rb") as f:
            G = pickle.load(f)
    # Convert to undirected simple graph for betweenness (include Person nodes or all nodes if kind not set)
    H = nx.Graph()
    for u, v, data in G.edges(data=True):
        kind_u = G.nodes[u].get("kind", "Person") if u in G.nodes else "Person"
        kind_v = G.nodes[v].get("kind", "Person") if v in G.nodes else "Person"
        if kind_u in ("Person", "Phone", "Account") and kind_v in ("Person", "Phone", "Account"):
            H.add_edge(u, v)
    if len(H.nodes) == 0:
        # Fallback: add all edges
        for u, v in G.edges():
            H.add_edge(u, v)
    if len(H.nodes) == 0:
        return []
    bet = nx.betweenness_centrality(H, normalized=True)
    deg = dict(H.degree())
    deg_cent = nx.degree_centrality(H)
    try:
        pagerank = nx.pagerank(H, alpha=0.85)
    except:
        pagerank = {n: 0 for n in H.nodes}
    res = []
    for nid in H.nodes:
        node_attr = G.nodes[nid] if nid in G.nodes else {}
        res.append({
            "id": nid,
            "name": node_attr.get("label", nid),
            "cell": node_attr.get("cell", "Unknown"),
            "role": node_attr.get("role", ""),
            "betweenness": round(bet.get(nid, 0.0), 4),
            "degree": deg.get(nid, 0),
            "degree_centrality": round(deg_cent.get(nid, 0.0), 4),
            "pagerank": round(pagerank.get(nid, 0.0), 4)
        })
    res.sort(key=lambda x: x["betweenness"], reverse=True)
    return res

def compute_centrality(graph_serial: Dict = None, pkl_path: Path = None) -> List[Dict]:
    return compute_centrality_networkx(graph_serial, pkl_path)


def compute_betweenness_centrality(graph_serial: Dict = None, pkl_path: Path = None) -> Dict[str, float]:
    """Compatibility shim for takedown_simulator: returns {id: betweenness}."""
    rows = compute_centrality_networkx(graph_serial, pkl_path)
    return {r["id"]: r.get("betweenness", 0.0) for r in rows}


def compute_pagerank(graph_serial: Dict = None, pkl_path: Path = None) -> Dict[str, float]:
    """Compatibility shim: returns {id: pagerank}."""
    rows = compute_centrality_networkx(graph_serial, pkl_path)
    return {r["id"]: r.get("pagerank", 0.0) for r in rows}
