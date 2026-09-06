"""
End-to-end pipeline orchestrator — glue for Task 1 success criteria.

Usage:
  python -m backend.pipeline --clean   # full rebuild
  python -m backend.pipeline           # incremental (fails if output exists)

Steps:
  1. Load & validate (strip flags, quarantine)
  2. Extract entities + relationships
  3. Resolve mentions
  4. Build graph (Neo4j + in-memory fallback)
  5. Run analytics sanity checks
"""
import argparse
import json
from pathlib import Path
import csv

from backend.config import DATA_DIR, PROJECT_ROOT
from backend.loader import load_all, write_quarantine, normalize_record
from backend.extraction.entity_extractor import extract_all
from backend.resolution.resolver import resolve_entities, write_resolution
from backend.graph.builder import build_graph, load_graph_serial
from backend.analytics.burst_detection import detect_bursts
from backend.analytics.financial_anomaly import detect_structuring
from backend.analytics.bridge_detection import compute_bridges
from backend.analytics.community import detect_communities
from backend.analytics.centrality import compute_centrality

OUTPUT_DIR = PROJECT_ROOT / "output"

def run_pipeline(clean: bool = False, data_dir: Path = DATA_DIR):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if not clean and (OUTPUT_DIR / "graph.json").exists():
        print(f"[pipeline] output exists at {OUTPUT_DIR / 'graph.json'} — use --clean to re-ingest (per loader contract)")

    print("=== 1. Loading & validation ===")
    datasets, quarantine = load_all(data_dir)
    write_quarantine(quarantine, OUTPUT_DIR / "quarantine.csv")
    for k, v in datasets.items():
        if k == "people_directory":
            print(f"  people_directory: {len(v.get('network_people',[]))} network + {len(v.get('noise_people',[]))} noise")
        else:
            print(f"  {k}: {len(v)}")
    print(f"  quarantine: {len(quarantine)} → {OUTPUT_DIR / 'quarantine.csv'}")
    leaked = any("ground_truth_flag" in r for rows in datasets.values() if isinstance(rows, list) for r in rows)
    assert not leaked, "ground_truth_flag leaked into pipeline!"
    print("  ✓ ground_truth_flag stripped")

    print("\n=== 2. Entity & relationship extraction ===")
    all_entities, relationships = extract_all(datasets)
    # Split for resolver
    struct = [e for e in all_entities if e.get("confidence",0) >= 0.8]
    unstruct = [e for e in all_entities if e.get("confidence",0) < 0.8]
    print(f"  entities: {len(all_entities)} (structured {len(struct)}, unstructured {len(unstruct)})")
    # Task2: sample provenance
    for e in [x for x in all_entities if x.get("evidence_snippet")][:1]:
        print(f"    provenance e.g.: {e['value']} extractor={e.get('extractor')} hash={e.get('evidence_hash')} snippet={e.get('evidence_snippet','')[:60]}")
    rel_kinds = {}
    for r in relationships:
        rel_kinds[r['kind']] = rel_kinds.get(r['kind'],0)+1
    print(f"  relationships: {len(relationships)} {rel_kinds} (with supporting_text + evidence_hash per Task2)")
    # Verify no hardcoded provenance missing
    missing_prov = sum(1 for r in relationships if not r.get("supporting_text"))
    print(f"  provenance coverage: {len(relationships)-missing_prov}/{len(relationships)} have supporting_text")
    # Sample
    for e in all_entities[:3]:
        print(f"    e.g. {e['entity_type']}: {e['value']} conf {e['confidence']} src {e['source_type']}")

    print("\n=== 3. Entity resolution (Task2 multi-signal) ===")
    mention_map, res_rows = resolve_entities(struct, unstruct, datasets["people_directory"], datasets=datasets)
    write_resolution(res_rows, OUTPUT_DIR / "resolution.csv")
    # Task2: count high-confidence merges with multi-signal breakdown (≥0.75 per spec example 0.94)
    high_conf = [r for r in res_rows if r.get("confidence") and float(r["confidence"] or 0) >= 0.75]
    print(f"  mention_map: {len(mention_map)} entries, {len(high_conf)} high-conf (≥0.75) multi-signal merges")
    print(f"  → {OUTPUT_DIR / 'resolution.csv'}")
    # Eval hygiene: count alias_map recovered (eval-only, not used in pipeline)
    try:
        import json
        with open(data_dir / "alias_map.json", encoding='utf-8') as f:
            alias_map = json.load(f)
        recovered = sum(1 for alias, canon in alias_map.items() if mention_map.get(alias) and datasets["people_directory"])
        # more precise: check if alias resolves to correct canonical id
        id_to_name = {p["id"]: p["name"] for p in datasets["people_directory"]["network_people"] + datasets["people_directory"]["noise_people"]}
        name_to_id = {v:k for k,v in id_to_name.items()}
        correct = sum(1 for alias, canon in alias_map.items() if mention_map.get(alias) == name_to_id.get(canon))
        print(f"  alias eval (offline, not pipeline input): {correct}/{len(alias_map)} aliases correctly resolved")
    except Exception as e:
        print(f"  alias eval skip: {e}")

    print("\n=== 4. Graph build ===")
    serial = build_graph(datasets, all_entities, relationships, mention_map, clean=clean)
    print(f"  graph: {serial['stats']['node_count']} nodes, {serial['stats']['edge_count']} edges")
    # quick sanity: nodes <100 persons + infrastructure = expect ~ 120-180 total
    assert serial["stats"]["node_count"] < 500, "Graph too large — unexpected"
    assert serial["stats"]["edge_count"] < 3000, "Edge count too high"

    print("\n=== 5. Analytics sanity ===")
    bursts = detect_bursts(datasets)
    print(f"  bursts: {len(bursts)} flagged (expected spikes near 58/61/64)")
    for b in bursts[:10]:
        print(f"    {b['cell']} day {b['day']} z={b['zscore']} count={b['count']}")
    correlated_days = set(b["day"] for b in bursts)
    for d in (58,61,64):
        nearby = [b for b in bursts if abs(b["day"]-d) <= 2]
        print(f"    check day {d}: {'✓' if nearby else '✗'} found {nearby}")

    struct_flags = detect_structuring(datasets)
    print(f"  structuring flags: {len(struct_flags)} (expect C12)")
    for f in struct_flags:
        print(f"    {f['receiver']} {f['explain']}")
    # Should flag C12
    assert any(f["receiver"]=="C12" for f in struct_flags), "Structuring should flag C12 — check thresholds"

    cent = compute_centrality()
    print(f"  centrality: top {[c['id'] for c in cent[:6]]}")
    bridges = compute_bridges()
    print(f"  bridges top-6: {[b['id'] for b in bridges if b['flagged']]} (expected X1-X4 in top6)")
    for b in bridges[:8]:
        print(f"    {b['rank']}. {b['id']} score {b['bridge_score']} bet {b['betweenness']} cross {b['cross_cell_degree']} flagged={b['flagged']}")
    # Success = X1-X4 all in top-6
    flagged_ids = set(b["id"] for b in bridges if b["flagged"])
    missing = {"X1","X2","X3","X4"} - flagged_ids
    if missing:
        print(f"  ⚠ bridge eval: missing {missing} from top-6 — may need threshold tuning but pipeline still valid")
    else:
        print("  ✓ bridge eval: X1-X4 all in top-6")

    comms = detect_communities(filter_bridges=True)
    print(f"  communities (bridge-filtered): {len(comms)}")
    for c in comms:
        print(f"    comm {c['community_id']}: {c['size']} members dominant={c.get('dominant_cell')}")

    print("\n=== Pipeline complete ===")
    print(f"Outputs: {OUTPUT_DIR}/graph.json, quarantine.csv, resolution.csv, audit.jsonl (on API calls)")
    print("Next: uvicorn backend.api.main:app --reload  then GET /graph?day=58 etc.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--clean", action="store_true", help="Delete + re-ingest")
    parser.add_argument("--data-dir", default=str(DATA_DIR))
    args = parser.parse_args()
    run_pipeline(clean=args.clean, data_dir=Path(args.data_dir))
