"""
Graph Builder — nodes/edges with provenance (source, timestamp, confidence).

Supports two backends:
  - Neo4j (if available) — MERGE via Cypher
  - In-memory NetworkX — serialized to output/graph.json + output/graph.pkl for API fallback

Every edge retains: source, source_type, day, timestamp, confidence, meta
"""
import json
import pickle
from pathlib import Path
from typing import Dict, List
from collections import defaultdict

try:
    import networkx as nx
    HAS_NX = True
except ImportError:
    HAS_NX = False
    nx = None

from backend.config import DATA_DIR, PROJECT_ROOT
from backend.graph.neo4j_client import get_driver, is_available
from backend.config import NEO4J_DATABASE

OUTPUT_DIR = PROJECT_ROOT / "output"
GRAPH_JSON = OUTPUT_DIR / "graph.json"

def build_in_memory_graph(datasets: Dict, entities: List[Dict], relationships: List[Dict], mention_map: Dict[str,str], output_dir: Path = None) -> Dict:
    """
    Build NetworkX graph + serializable dict for API.
    Nodes: Person, Phone, Account, Location, FIR, Post, Event
    Edges with provenance.

    output_dir overrides the module-level OUTPUT_DIR/GRAPH_JSON so
    per-investigation builds can write to their own directory without
    mutating shared globals (concurrency-safe). Defaults to OUTPUT_DIR.
    """
    if not HAS_NX:
        raise RuntimeError("networkx required for in-memory graph")

    G = nx.MultiDiGraph()

    # 1. People from people_directory
    pd = datasets.get("people_directory", {})
    for p in pd.get("network_people", []) + pd.get("noise_people", []):
        G.add_node(p["id"], label=p["name"], kind="Person", cell=p.get("cell"), role=p.get("role"),
                   phone=p.get("phone"), account=p.get("account"), degree=0)
        # Phone/Account nodes + OWN edges (with provenance like all other edges)
        if p.get("phone"):
            pid = f"PHONE_{p['phone']}"
            if not G.has_node(pid):
                G.add_node(pid, label=p["phone"], kind="Phone", cell="Phone")
            G.add_edge(p["id"], pid, kind="OWNS_PHONE", source="people_directory", source_type="people_directory", confidence=1.0,
                       supporting_text=f"{p['id']} owns phone {p['phone']}", evidence_hash="", extractor="canonical")
            G.add_edge(pid, p["id"], kind="OWNED_BY", source="people_directory", source_type="people_directory", confidence=1.0,
                       supporting_text=f"phone {p['phone']} owned by {p['id']}", evidence_hash="", extractor="canonical")
        if p.get("account"):
            aid = f"ACCT_{p['account']}"
            if not G.has_node(aid):
                G.add_node(aid, label=p["account"], kind="Account", cell="Account")
            G.add_edge(p["id"], aid, kind="OWNS_ACCOUNT", source="people_directory", source_type="people_directory", confidence=1.0,
                       supporting_text=f"{p['id']} owns account {p['account']}", evidence_hash="", extractor="canonical")

    # NOTE: Event nodes are NOT built from ground_truth_network.json (eval-only).
    # Ground truth must never enter the detection pipeline. Temporal bursts are
    # detected from CDR data by burst_detection.py instead.

    # 3. FIR nodes
    for row in datasets.get("firs", []):
        fid = row.get("fir_id")
        if not fid:
            continue
        G.add_node(fid, label=f"FIR {fid}", kind="FIR", cell="FIR", day=row.get("day"), location=row.get("location"))
        # narrative mentions -> MENTIONED_IN edges via resolved mentions (full provenance)
        narrative = row.get("narrative","")
        for mention, canonical in mention_map.items():
            if mention in narrative and G.has_node(canonical) and canonical != mention:
                # avoid excessive edges — only if high-confidence resolution already logged
                idx = narrative.find(mention)
                snippet = narrative[max(0, idx-60): idx+len(mention)+60].strip().replace("\n", " ") if idx != -1 else mention
                import hashlib as _hl
                h = _hl.sha256(snippet.encode()).hexdigest()[:16]
                G.add_edge(canonical, fid, kind="MENTIONED_IN", source=fid, source_type="fir", day=row.get("day"),
                           timestamp=row.get("date"), confidence=0.6, raw_text=mention,
                           supporting_text=snippet, evidence_hash=h, extractor="fir_mention")

    # 4. Surveillance / Intel nodes
    for row in datasets.get("surveillance_reports", []):
        rid = row.get("report_id")
        if rid:
            G.add_node(rid, label=rid, kind="Surveillance", cell="Surveillance", day=row.get("day"), location=row.get("location"))

    for row in datasets.get("intelligence_reports", []):
        rid = row.get("report_id")
        if rid:
            G.add_node(rid, label=rid, kind="Intel", cell="Intel", day=row.get("day"))

    # 5. Relationships from extraction (CALLED/TRANSACTED/LOCATED_AT) — strongest provenance
    for rel in relationships:
        src = rel.get("src")
        dst = rel.get("dst")
        if not src or not dst:
            continue
        # Resolve src/dst via mention_map if they are alias mentions (txn/cdr ids are canonical already)
        src_resolved = mention_map.get(src, src)
        dst_resolved = mention_map.get(dst, dst)
        # Only add edge if both nodes exist (or create placeholder for missing like Location strings)
        if not G.has_node(src_resolved):
            # Locations / FIRs may appear as dst strings — create if needed
            if isinstance(src_resolved, str) and ("Ward" in src_resolved or "Colony" in src_resolved or "Road" in src_resolved):
                G.add_node(src_resolved, label=src_resolved, kind="Location", cell="Location")
            else:
                continue
        if not G.has_node(dst_resolved):
            if isinstance(dst_resolved, str) and ("Ward" in dst_resolved or "Colony" in dst_resolved or "Road" in dst_resolved):
                G.add_node(dst_resolved, label=dst_resolved, kind="Location", cell="Location")
            else:
                continue
        # Task2 provenance: supporting_text, evidence_hash, extractor retained per edge
        G.add_edge(src_resolved, dst_resolved, kind=rel.get("kind"), source=rel.get("source"), source_type=rel.get("source_type"),
                   day=rel.get("day"), timestamp=rel.get("timestamp"), confidence=rel.get("confidence",0.8),
                   supporting_text=rel.get("supporting_text",""), evidence_hash=rel.get("evidence_hash",""), extractor=rel.get("extractor",""),
                   meta=rel.get("meta",{}))

    # Also add Post nodes (with provenance like all other edges)
    for row in datasets.get("social_posts", []):
        pid = row.get("post_id")
        author = row.get("person_id")
        author_res = mention_map.get(author, author) if author else None
        if pid:
            G.add_node(pid, label=pid, kind="Post", cell="Post", day=row.get("day"), handle=row.get("handle"))
            if author_res and G.has_node(author_res):
                snippet = (row.get("post_text") or "")[:160].replace("\n", " ")
                import hashlib as _hl2
                h2 = _hl2.sha256((snippet or pid).encode()).hexdigest()[:16]
                G.add_edge(author_res, pid, kind="AUTHORED", source=pid, source_type="social_post", day=row.get("day"),
                           timestamp=row.get("timestamp"), confidence=0.95,
                           supporting_text=snippet, evidence_hash=h2, extractor="social_authored")
            # mentioned aliases in post_text already handled via narrative mention loop if needed — simplified here

    # Compute degree for UI filtering (isolated noise filter degree<2)
    for n in G.nodes():
        G.nodes[n]["degree"] = G.degree(n)

    # Serialize for API
    # Convert to JSON-safe
    nodes = []
    for nid, attrs in G.nodes(data=True):
        nodes.append({"id": nid, **attrs})
    edges = []
    for u, v, key, attrs in G.edges(keys=True, data=True):
        edges.append({"src": u, "dst": v, "kind": attrs.get("kind"), "source": attrs.get("source"),
                      "source_type": attrs.get("source_type"), "day": attrs.get("day"),
                      "confidence": attrs.get("confidence"), "supporting_text": attrs.get("supporting_text",""),
                      "evidence_hash": attrs.get("evidence_hash",""), "extractor": attrs.get("extractor",""),
                      "meta": attrs.get("meta",{})})

    serial = {"nodes": nodes, "edges": edges, "stats": {"node_count": len(nodes), "edge_count": len(edges)}}

    out_dir = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    graph_json = out_dir / "graph.json"
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(graph_json, "w", encoding='utf-8') as f:
        json.dump(serial, f, indent=2, default=str)
    # also pickle for fast analytics
    with open(out_dir / "graph.pkl", "wb") as f:
        pickle.dump(G, f)

    print(f"[graph] built in-memory: {len(nodes)} nodes, {len(edges)} edges → {graph_json}")
    return serial

def build_neo4j_graph(datasets: Dict, entities: List[Dict], relationships: List[Dict], mention_map: Dict, iid: str = "default"):
    drv = get_driver()
    if not drv:
        return None
    # Schema first
    from backend.graph.schema import ensure_schema, clear_graph
    # Note: caller decides --clean
    ensure_schema()

    # Batch MERGE people
    pd = datasets.get("people_directory", {})
    with drv.session(database=NEO4J_DATABASE) as session:
        for p in pd.get("network_people", []) + pd.get("noise_people", []):
            session.run(
                "MERGE (n:Person {id:$id, investigation_id:$iid}) SET n.name=$name, n.role=$role, n.cell=$cell, n.phone=$phone, n.account=$account",
                id=p["id"], iid=iid, name=p["name"], role=p["role"], cell=p["cell"], phone=p.get("phone"), account=p.get("account")
            )
            if p.get("phone"):
                session.run("MERGE (ph:Phone {number:$num, investigation_id:$iid}) MERGE (p:Person {id:$id, investigation_id:$iid}) MERGE (p)-[:OWNS_PHONE {confidence:1.0, source:'people_directory'}]->(ph)",
                            num=p["phone"], iid=iid, id=p["id"])
            if p.get("account"):
                session.run("MERGE (a:Account {id:$aid, investigation_id:$iid}) MERGE (p:Person {id:$id, investigation_id:$iid}) MERGE (p)-[:OWNS_ACCOUNT {confidence:1.0, source:'people_directory'}]->(a)",
                            aid=p["account"], iid=iid, id=p["id"])
        # Relationships
        for rel in relationships:
            src = mention_map.get(rel.get("src"), rel.get("src"))
            dst = mention_map.get(rel.get("dst"), rel.get("dst"))
            if not src or not dst:
                continue
            kind = rel.get("kind")
            # sanitize kind for Cypher (must be uppercase, no spaces)
            # Includes unstructured kinds from Task2 relationship extraction
            if kind not in ("CALLED","CALLS","TRANSACTED","TRANSFERRED_TO","LOCATED_AT","AUTHORED",
                            "PARTICIPATED_IN","OWNS","OWNS_PHONE","OWNS_ACCOUNT","OWNED_BY","MENTIONED_IN",
                            "ASSOCIATED_WITH","MET","WORKS_FOR"):
                continue
            # need to know node labels — try Person first, fallback to generic
            # We use MERGE on id property agnostic: MATCH then CREATE relationship via separate query
            try:
                session.run(
                    f"MATCH (a {{id:$src, investigation_id:$iid}}), (b {{id:$dst, investigation_id:$iid}}) MERGE (a)-[r:{kind} {{source:$source, source_type:$stype, day:$day, confidence:$conf, evidence_hash:$ehash}}]->(b) "
                    "SET r.timestamp=$ts, r.supporting_text=$stext, r.extractor=$ext",
                    src=src, dst=dst, iid=iid, source=rel.get("source"), stype=rel.get("source_type"), day=rel.get("day"), conf=rel.get("confidence",0.8), ts=str(rel.get("timestamp")),
                    stext=rel.get("supporting_text","")[:300], ehash=rel.get("evidence_hash",""), ext=rel.get("extractor","")
                )
            except Exception as e:
                # Neo4j requires label on at least one side — fallback: create Person placeholder
                print(f"[neo4j] skip edge {src}->{dst} {kind}: {e}")

        print(f"[graph] Neo4j populated: {len(pd.get('network_people',[]))+len(pd.get('noise_people',[]))} persons + {len(relationships)} rels for iid {iid}")

def build_graph(datasets: Dict, entities: List[Dict], relationships: List[Dict], mention_map: Dict, clean: bool=False, iid: str = "default", output_dir: Path = None):
    """
    Unified entry — tries Neo4j, always builds in-memory fallback.
    Returns serial dict.

    output_dir overrides the module-level OUTPUT_DIR/GRAPH_JSON for
    per-investigation builds. It is passed as a parameter (not via global
    mutation) so concurrent requests cannot corrupt each other's output.
    Defaults to OUTPUT_DIR.
    """
    out_dir = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    graph_json = out_dir / "graph.json"
    if clean:
        from backend.graph.schema import clear_graph
        if is_available():
            clear_graph()
        # also clear output graph
        if graph_json.exists():
            graph_json.unlink()

    # Always build in-memory (source of truth for API fallback)
    serial = build_in_memory_graph(datasets, entities, relationships, mention_map, output_dir=out_dir)

    # Opportunistically push to Neo4j if available
    if is_available():
        try:
            build_neo4j_graph(datasets, entities, relationships, mention_map, iid=iid)
        except Exception as e:
            print(f"[graph] Neo4j push failed, using fallback: {e}")

    return serial

def load_graph_serial() -> Dict:
    if GRAPH_JSON.exists():
        with open(GRAPH_JSON, encoding='utf-8') as f:
            return json.load(f)
    return {"nodes": [], "edges": [], "stats": {"node_count":0,"edge_count":0}}
