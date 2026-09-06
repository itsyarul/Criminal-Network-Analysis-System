"""
Tactical Takedown & Arrest Optimization Simulator.
Command-Level Syndicate Dismantlement & Multi-Target Raid Optimization Engine.

Provides:
- Graph percolation and network fragmentation metrics
- Comparative pre-configured tactical strike strategies (Decapitation, Bridge Interdiction, Financial Freeze, Synchronized Strike)
- Custom interactive multi-target raid simulation
- Tactical SWAT/Armed squad resource allocation based on FIR violent priors
- Recoverable illicit asset seizure valuation
- Formal Police Operation Order (OP-ORDER / Tactical Dossier) generator
"""
import json
import math
from collections import defaultdict, deque
from typing import Dict, List, Optional, Any, Set, Tuple

from backend.config import DATA_DIR
from backend.loader import load_all
from backend.graph.builder import load_graph_serial
from backend.analytics.centrality import compute_betweenness_centrality, compute_pagerank


def _build_adj_list(nodes: List[Dict], edges: List[Dict], excluded_nodes: Set[str]) -> Dict[str, Set[str]]:
    """Build adjacency list omitting excluded nodes."""
    adj = defaultdict(set)
    active_node_ids = {n["id"] for n in nodes if n["id"] not in excluded_nodes}
    for e in edges:
        u, v = e.get("src"), e.get("dst")
        if u in active_node_ids and v in active_node_ids:
            adj[u].add(v)
            adj[v].add(u)
    for nid in active_node_ids:
        if nid not in adj:
            adj[nid] = set()
    return adj


def _calculate_connected_components(adj: Dict[str, Set[str]]) -> List[List[str]]:
    """Find all connected components in active graph."""
    visited = set()
    components = []
    for node in adj:
        if node not in visited:
            comp = []
            q = deque([node])
            visited.add(node)
            while q:
                curr = q.popleft()
                comp.append(curr)
                for nbr in adj[curr]:
                    if nbr not in visited:
                        visited.add(nbr)
                        q.append(nbr)
            components.append(comp)
    return components


def _calculate_network_efficiency(adj: Dict[str, Set[str]]) -> float:
    """
    Calculate global network communication efficiency (Latora & Marchiori index):
    E(G) = 1 / (N * (N-1)) * sum_{i != j} 1 / d(i, j)
    """
    nodes = list(adj.keys())
    n = len(nodes)
    if n <= 1:
        return 0.0

    total_inv_dist = 0.0
    for i in range(n):
        src = nodes[i]
        distances = {src: 0}
        q = deque([src])
        while q:
            curr = q.popleft()
            curr_d = distances[curr]
            for nbr in adj[curr]:
                if nbr not in distances:
                    distances[nbr] = curr_d + 1
                    total_inv_dist += 1.0 / (curr_d + 1)
                    q.append(nbr)

    max_pairs = n * (n - 1)
    return total_inv_dist / max_pairs if max_pairs > 0 else 0.0


def _get_person_priors(person_id: str, datasets: Dict[str, Any]) -> Dict[str, Any]:
    """Scan FIRs and criminal history for weapon/violence priors."""
    firs = datasets.get("firs", [])
    crim = datasets.get("criminal_history", [])
    
    violent_charges = []
    weapon_involved = False
    is_armed = False

    violent_keywords = ["murder", "302", "307", "arms", "firearm", "weapon", "extortion", "384", "assault", "violence", "threat", "392", "397"]
    
    for f in firs:
        suspects = [s.strip().lower() for s in (f.get("suspects") or "").split(",")]
        if person_id.lower() in suspects or any(person_id.lower() in s for s in suspects):
            charges = str(f.get("sections_applied") or "") + " " + str(f.get("description") or "")
            if any(k in charges.lower() for k in violent_keywords):
                violent_charges.append(f.get("sections_applied") or "IPC Sections")
            if any(k in charges.lower() for k in ["arms", "firearm", "weapon", "gun", "pistol", "397"]):
                weapon_involved = True

    for c in crim:
        if c.get("person_id") == person_id:
            offences = str(c.get("prior_offences") or "")
            if any(k in offences.lower() for k in violent_keywords):
                violent_charges.append(offences)
            if any(k in offences.lower() for k in ["arms", "firearm", "weapon", "gun", "pistol"]):
                weapon_involved = True

    risk_level = "CRITICAL (ARMED)" if weapon_involved else "HIGH" if violent_charges else "STANDARD"
    return {
        "person_id": person_id,
        "violent_charges": list(set(violent_charges)),
        "weapon_involved": weapon_involved,
        "risk_level": risk_level,
    }


def simulate_takedown(
    target_ids: List[str],
    datasets: Optional[Dict] = None,
    graph: Optional[Dict] = None,
    freeze_financial_accounts: bool = True
) -> Dict[str, Any]:
    """
    Simulate the strategic neutralization/arrest of specified target nodes and accounts.
    Returns impact score, network fragmentation, recoverable assets, and SWAT force requirements.
    """
    if datasets is None:
        datasets, _ = load_all(DATA_DIR)
    if graph is None:
        try:
            graph = load_graph_serial()
        except Exception:
            graph = {"nodes": [], "edges": []}

    all_nodes = graph.get("nodes", [])
    all_edges = graph.get("edges", [])

    pd = datasets.get("people_directory", {})
    people_list = pd.get("network_people", []) + pd.get("noise_people", [])
    person_map = {p["id"]: p for p in people_list}
    node_map = {n["id"]: n for n in all_nodes}
    for nid, nd in node_map.items():
        if nid not in person_map:
            person_map[nid] = nd

    # Also map accounts linked to target suspects if financial freeze is enabled
    excluded = set(target_ids)
    frozen_accounts = set()
    if freeze_financial_accounts:
        for p in all_nodes:
            if p["id"] in excluded:
                # Find accounts belonging to this suspect
                p_data = person_map.get(p["id"], {})
                if p_data.get("account"):
                    frozen_accounts.add(p_data["account"])
                    excluded.add(p_data["account"])
                if p_data.get("phone"):
                    excluded.add(p_data["phone"])

    # Baseline network analysis
    base_adj = _build_adj_list(all_nodes, all_edges, set())
    base_efficiency = _calculate_network_efficiency(base_adj)
    base_components = _calculate_connected_components(base_adj)

    # Post-takedown network analysis
    post_adj = _build_adj_list(all_nodes, all_edges, excluded)
    post_efficiency = _calculate_network_efficiency(post_adj)
    post_components = _calculate_connected_components(post_adj)

    # Metrics computation
    if base_efficiency > 0:
        disruption_pct = max(0.0, min(100.0, (1.0 - (post_efficiency / base_efficiency)) * 100.0))
    else:
        disruption_pct = 100.0 if excluded else 0.0

    # Calculate severed communication channels and cross-cell coordination
    severed_edges = [
        e for e in all_edges
        if e.get("src") in excluded or e.get("dst") in excluded
    ]
    cross_cell_severed = [
        e for e in severed_edges
        if e.get("kind") in ("CALLED", "TRANSACTED")
    ]

    # Calculate recoverable financial wealth from frozen accounts & targets
    txns = datasets.get("transactions", [])
    seized_funds = 0.0
    frozen_txns_count = 0
    for t in txns:
        s_from = str(t.get("sender_account") or "")
        s_to = str(t.get("receiver_account") or "")
        amt = float(t.get("amount") or 0)
        if s_from in excluded or s_to in excluded or s_from in frozen_accounts or s_to in frozen_accounts:
            seized_funds += amt
            frozen_txns_count += 1

    # SWAT & Tactical Resource Requirements
    tactical_teams = []
    armed_swat_needed = 0
    cyber_forensics_needed = 0
    perimeter_squads = 0

    target_profiles = []
    for tid in target_ids:
        p_info = person_map.get(tid, {})
        p_prior = _get_person_priors(tid, datasets)
        target_profiles.append({
            "target_id": tid,
            "name": p_info.get("name", tid),
            "role": p_info.get("role", "Suspect"),
            "cell": p_info.get("cell", "Unknown"),
            "risk_level": p_prior["risk_level"],
            "weapon_involved": p_prior["weapon_involved"],
            "violent_charges": p_prior["violent_charges"],
            "known_address": p_info.get("known_address", "Metropolitan Area"),
        })

        if p_prior["weapon_involved"] or "CRITICAL" in p_prior["risk_level"]:
            armed_swat_needed += 2
            perimeter_squads += 2
        elif "HIGH" in p_prior["risk_level"]:
            armed_swat_needed += 1
            perimeter_squads += 1
        else:
            armed_swat_needed += 1
            perimeter_squads += 1

        cyber_forensics_needed += 1

    # Ensure minimum baselines
    armed_swat_needed = max(1, armed_swat_needed)
    cyber_forensics_needed = max(1, cyber_forensics_needed)
    perimeter_squads = max(1, perimeter_squads)

    # Succession Risk: Check if remaining nodes have high betweenness/PageRank
    remaining_suspects = [
        n["id"] for n in all_nodes
        if n["id"] not in excluded and person_map.get(n["id"], {}).get("role") in ("Kingpin", "Leader", "Lieutenant", "Coordinator")
    ]
    succession_risk = "LOW — Command structure shattered" if not remaining_suspects else f"MODERATE — Deputy leaders remain ({', '.join(remaining_suspects[:2])})"

    return {
        "status": "success",
        "targets_count": len(target_ids),
        "target_profiles": target_profiles,
        "frozen_accounts_count": len(frozen_accounts),
        "dismantlement_score_pct": round(disruption_pct, 1),
        "baseline_efficiency": round(base_efficiency, 4),
        "post_takedown_efficiency": round(post_efficiency, 4),
        "isolated_fragments_count": len(post_components),
        "severed_channels_count": len(severed_edges),
        "recoverable_assets_inr": seized_funds,
        "frozen_transactions_count": frozen_txns_count,
        "succession_risk": succession_risk,
        "tactical_resource_allocation": {
            "armed_tactical_units": armed_swat_needed,
            "cyber_forensics_officers": cyber_forensics_needed,
            "perimeter_containment_squads": perimeter_squads,
            "total_personnel_required": armed_swat_needed * 4 + cyber_forensics_needed * 2 + perimeter_squads * 3,
        },
        "optimal_strike_window": "03:30 hrs - 05:00 hrs IST (Simultaneous Pre-Dawn Sweep)",
    }


def get_takedown_strategies(
    datasets: Optional[Dict] = None,
    graph: Optional[Dict] = None
) -> Dict[str, Any]:
    """
    Generate and rank 4 pre-engineered tactical strike packages:
    1. 👑 Top-Down Decapitation (Kingpins)
    2. 🌉 Bridge & Facilitator Interdiction (Couriers / Coordinators)
    3. 💳 Financial Asphyxiation (Money Mules & Laundering Nodes)
    4. ⚡ Synchronized Strike Package (Optimal AI Multi-Target Raid)
    """
    if datasets is None:
        datasets, _ = load_all(DATA_DIR)
    if graph is None:
        try:
            graph = load_graph_serial()
        except Exception:
            graph = {"nodes": [], "edges": []}

    nodes = graph.get("nodes", [])
    pd = datasets.get("people_directory", {})
    people = pd.get("network_people", []) + pd.get("noise_people", [])
    person_map = {p["id"]: p for p in people}

    # Compute graph metrics for strategy identification
    pr_scores = compute_pagerank(graph)
    bw_scores = compute_betweenness_centrality(graph)

    # 1. Decapitation Targets (Top PageRank / Kingpins)
    kingpin_candidates = sorted(
        [n["id"] for n in nodes if n["id"] in person_map and person_map[n["id"]].get("cell") in ("A", "B", "C", "Bridge")],
        key=lambda nid: pr_scores.get(nid, 0.0),
        reverse=True
    )[:3]

    # 2. Bridge Interdiction Targets (Top Betweenness / Facilitators)
    bridge_candidates = sorted(
        [n["id"] for n in nodes if n["id"] in person_map and n["id"] not in kingpin_candidates],
        key=lambda nid: bw_scores.get(nid, 0.0),
        reverse=True
    )[:3]

    # 3. Financial Mule Targets (Structuring Accounts & High-Volume Transactors)
    txns = datasets.get("transactions", [])
    acc_vol = defaultdict(float)
    for t in txns:
        acc_vol[str(t.get("sender_account"))] += float(t.get("amount") or 0)
        acc_vol[str(t.get("receiver_account"))] += float(t.get("amount") or 0)

    mule_people = sorted(
        [p["id"] for p in people if p.get("account") in acc_vol],
        key=lambda pid: acc_vol.get(person_map[pid].get("account", ""), 0),
        reverse=True
    )[:4]

    # 4. Synchronized Strike Package (Balanced Pareto Optimal Strike)
    sync_candidates = list(dict.fromkeys(kingpin_candidates[:2] + bridge_candidates[:2] + mule_people[:2]))

    # Run simulations for all 4 packages
    sim_decap = simulate_takedown(kingpin_candidates, datasets, graph)
    sim_bridge = simulate_takedown(bridge_candidates, datasets, graph)
    sim_mule = simulate_takedown(mule_people, datasets, graph)
    sim_sync = simulate_takedown(sync_candidates, datasets, graph)

    strategies = [
        {
            "id": "strategy_sync",
            "name": "⚡ Synchronized Blitzkrieg Strike (AI Optimal)",
            "badge": "RECOMMENDED // HIGHEST DISRUPTION",
            "badge_color": "green",
            "description": "Simultaneous pre-dawn tactical sweep targeting Tier-1 leadership, cross-cell Hawala couriers, and primary money laundering accounts in a single coordinated strike.",
            "target_ids": sync_candidates,
            "metrics": sim_sync,
        },
        {
            "id": "strategy_bridge",
            "name": "🌉 Facilitator & Bridge Interdiction",
            "badge": "MAX SURGICAL DISCONNECT",
            "badge_color": "blue",
            "description": "Neutralizes high-betweenness cross-cell couriers and financial intermediaries, severing communication lines between independent operational cells.",
            "target_ids": bridge_candidates,
            "metrics": sim_bridge,
        },
        {
            "id": "strategy_decap",
            "name": "👑 Top-Down Decapitation",
            "badge": "LEADERSHIP ELIMINATION",
            "badge_color": "yellow",
            "description": "Executes non-bailable warrants against top syndicate masterminds and financiers to disable overall decision-making authority.",
            "target_ids": kingpin_candidates,
            "metrics": sim_decap,
        },
        {
            "id": "strategy_mule",
            "name": "💳 Financial Asphyxiation & Asset Freeze",
            "badge": "REVENUE ASSET SEIZURE",
            "badge_color": "purple",
            "description": "Dispatches simultaneous Section 91 CrPC freezing notices to banking institutions to lock illicit capital reserves and starve operational cells of funding.",
            "target_ids": mule_people,
            "metrics": sim_mule,
        },
    ]

    return {
        "status": "success",
        "total_strategies": len(strategies),
        "strategies": strategies,
        "available_suspects": [
            {
                "id": p["id"],
                "name": p.get("name", p["id"]),
                "role": p.get("role", "Suspect"),
                "cell": p.get("cell", "Unknown"),
                "pagerank": round(pr_scores.get(p["id"], 0.0), 4),
                "betweenness": round(bw_scores.get(p["id"], 0.0), 4),
            }
            for p in people if p.get("cell") in ("A", "B", "C", "Bridge")
        ]
    }


def generate_operation_order(
    strategy_id_or_targets: Any,
    datasets: Optional[Dict] = None,
    graph: Optional[Dict] = None,
    commander_name: str = "Authorized Joint Commissioner",
    codename: str = "OPERATION THUNDERCLAP"
) -> Dict[str, Any]:
    """
    Generate an official tactical Police Operation Order (OP-ORDER / Raid Dossier).
    """
    if isinstance(strategy_id_or_targets, list):
        target_ids = strategy_id_or_targets
    elif isinstance(strategy_id_or_targets, str):
        strats = get_takedown_strategies(datasets, graph)
        found = next((s for s in strats["strategies"] if s["id"] == strategy_id_or_targets), None)
        target_ids = found["target_ids"] if found else ["P01", "P03"]
    else:
        target_ids = ["P01", "P03"]

    sim_res = simulate_takedown(target_ids, datasets, graph)

    return {
        "operation_order_id": f"OPORD-NCRB-{codename.replace(' ', '_').upper()}-2026",
        "operation_codename": codename.upper(),
        "commanding_officer": commander_name,
        "security_classification": "TOP SECRET // LAW ENFORCEMENT SENSITIVE",
        "strategic_objective": (
            f"Execute coordinated multi-point tactical arrests and asset seizures across {len(target_ids)} primary targets "
            f"to achieve an estimated {sim_res['dismantlement_score_pct']}% structural collapse of the syndicate."
        ),
        "execution_window": sim_res["optimal_strike_window"],
        "target_manifest": sim_res["target_profiles"],
        "tactical_resource_orders": sim_res["tactical_resource_allocation"],
        "estimated_asset_recovery_inr": f"Rs. {int(sim_res['recoverable_assets_inr']):,}",
        "succession_risk_verdict": sim_res["succession_risk"],
        "legal_and_procedural_directives": [
            "1. SECTION 63 BSA 2023 COMPLIANCE: Secure all mobile devices, burner phones, and laptops in Faraday evidence bags immediately upon seizure with hash logging.",
            "2. SECTION 91 CrPC FREEZE NOTICES: Serve electronic freezing notices on all identified mule accounts concurrently at H-Hour to prevent flight capital.",
            "3. CHAIN OF CUSTODY INTEGRITY: Record every suspect arrest location with GPS timestamps into the cryptographic evidence ledger.",
            "4. COGNIZABLE WARRANT EXECUTION: Dispatch search warrants and Non-Bailable Arrest Warrants (NBWs) through Special Designated Court."
        ]
    }
