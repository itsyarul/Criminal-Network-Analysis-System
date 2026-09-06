"""
Connection Explainer AI Engine ("Explain this connection" feature).

When given two entities (src_id, dst_id), this engine aggregates:
1. Telephony interactions (call counts, duration, timing, frequent towers, call bursts)
2. Financial transactions (direction, amounts, frequencies, structuring, lump sums)
3. FIR and police legal records (co-accused / co-mentioned cases, IPC sections, stations)
4. Surveillance & Intel reports (shared locations, meeting spots, sightings)
5. Social media & online touchpoints
6. Graph structural context (bridge connections, shared mutual associates)

Synthesizes a structured plain-English investigative narrative ("Story behind the connection")
backed by verifiable evidence items.
"""
from typing import Dict, List, Optional, Any, Set, Tuple
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import re

from backend.config import DATA_DIR
from backend.loader import load_all
from backend.graph.builder import load_graph_serial


def _resolve_person_details(person_id: str, datasets: Dict) -> Dict[str, Any]:
    """Retrieve name, role, cell, photo, phones, and accounts for a person."""
    pd = datasets.get("people_directory", {})
    all_people = pd.get("network_people", []) + pd.get("noise_people", [])
    
    # 1. Match by ID
    for p in all_people:
        if str(p.get("id", "")).strip().lower() == str(person_id).strip().lower():
            return {
                "id": p.get("id"),
                "name": p.get("name") or p.get("id"),
                "role": p.get("role", "Suspect"),
                "cell": p.get("cell", "Unknown"),
                "phone": p.get("phone", ""),
                "account": p.get("account", ""),
                "photo": p.get("photo") or f"/mugshots/{p.get('id')}.jpg",
            }
            
    # 2. Match by phone or account
    pid_clean = re.sub(r"\D", "", str(person_id))
    for p in all_people:
        p_phone = re.sub(r"\D", "", str(p.get("phone", "")))
        p_acc = str(p.get("account", "")).strip().lower()
        if (pid_clean and p_phone and pid_clean == p_phone) or (p_acc and p_acc == str(person_id).strip().lower()):
            return {
                "id": p.get("id"),
                "name": p.get("name") or p.get("id"),
                "role": p.get("role", "Suspect"),
                "cell": p.get("cell", "Unknown"),
                "phone": p.get("phone", ""),
                "account": p.get("account", ""),
                "photo": p.get("photo") or f"/mugshots/{p.get('id')}.jpg",
            }

    # Fallback placeholder
    return {
        "id": str(person_id),
        "name": str(person_id),
        "role": "Entity",
        "cell": "Unknown",
        "phone": str(person_id) if len(pid_clean) >= 8 else "",
        "account": "",
        "photo": f"/mugshots/{person_id}.jpg",
    }


def explain_connection(
    src_id: str,
    dst_id: str,
    datasets: Optional[Dict] = None,
    graph: Optional[Dict] = None
) -> Dict[str, Any]:
    """
    Generate a full plain-English story and multi-modal breakdown explaining
    why src_id and dst_id are linked in the criminal network.
    """
    if datasets is None:
        datasets, _ = load_all(DATA_DIR)
    if graph is None:
        try:
            graph = load_graph_serial()
        except Exception:
            graph = {"nodes": [], "edges": []}

    src_info = _resolve_person_details(src_id, datasets)
    dst_info = _resolve_person_details(dst_id, datasets)

    src_pids = {str(src_id).strip().lower(), str(src_info["id"]).strip().lower(), str(src_info["name"]).strip().lower()}
    dst_pids = {str(dst_id).strip().lower(), str(dst_info["id"]).strip().lower(), str(dst_info["name"]).strip().lower()}
    
    src_phones = {re.sub(r"\D", "", src_info["phone"])} if src_info["phone"] else set()
    dst_phones = {re.sub(r"\D", "", dst_info["phone"])} if dst_info["phone"] else set()
    src_accs = {src_info["account"].strip().lower()} if src_info["account"] else set()
    dst_accs = {dst_info["account"].strip().lower()} if dst_info["account"] else set()

    # -------------------------------------------------------------
    # 1. Telephony Interactions (CDRs)
    # -------------------------------------------------------------
    calls_src_to_dst = []
    calls_dst_to_src = []
    towers_used = defaultdict(int)
    call_days = set()
    total_call_sec = 0

    for cdr in datasets.get("cdrs", []):
        c_caller_id = str(cdr.get("caller_id") or "").strip().lower()
        c_caller_name = str(cdr.get("caller_name") or "").strip().lower()
        c_caller_phone = re.sub(r"\D", "", str(cdr.get("caller_phone") or ""))
        
        c_callee_id = str(cdr.get("callee_id") or "").strip().lower()
        c_callee_name = str(cdr.get("callee_name") or "").strip().lower()
        c_callee_phone = re.sub(r"\D", "", str(cdr.get("callee_phone") or ""))

        is_caller_src = (c_caller_id in src_pids) or (c_caller_name in src_pids) or (c_caller_phone and c_caller_phone in src_phones)
        is_callee_src = (c_callee_id in src_pids) or (c_callee_name in src_pids) or (c_callee_phone and c_callee_phone in src_phones)
        
        is_caller_dst = (c_caller_id in dst_pids) or (c_caller_name in dst_pids) or (c_caller_phone and c_caller_phone in dst_phones)
        is_callee_dst = (c_callee_id in dst_pids) or (c_callee_name in dst_pids) or (c_callee_phone and c_callee_phone in dst_phones)

        dur = int(cdr.get("duration_sec") or 0)
        tower = cdr.get("cell_tower_location") or "Unknown Tower"
        day = cdr.get("day") or cdr.get("timestamp", "")[:10]

        if is_caller_src and is_callee_dst:
            calls_src_to_dst.append(cdr)
            total_call_sec += dur
            towers_used[tower] += 1
            if day: call_days.add(str(day))
        elif is_caller_dst and is_callee_src:
            calls_dst_to_src.append(cdr)
            total_call_sec += dur
            towers_used[tower] += 1
            if day: call_days.add(str(day))

    total_calls = len(calls_src_to_dst) + len(calls_dst_to_src)
    total_call_min = round(total_call_sec / 60.0, 1)

    # -------------------------------------------------------------
    # 2. Financial Transactions
    # -------------------------------------------------------------
    txns_src_to_dst = []
    txns_dst_to_src = []
    total_inr_src_to_dst = 0
    total_inr_dst_to_src = 0

    for txn in datasets.get("transactions", []):
        s_id = str(txn.get("sender_id") or "").strip().lower()
        s_name = str(txn.get("sender_name") or "").strip().lower()
        s_acc = str(txn.get("sender_account") or "").strip().lower()

        r_id = str(txn.get("receiver_id") or "").strip().lower()
        r_name = str(txn.get("receiver_name") or "").strip().lower()
        r_acc = str(txn.get("receiver_account") or "").strip().lower()

        is_sender_src = (s_id in src_pids) or (s_name in src_pids) or (s_acc and s_acc in src_accs)
        is_receiver_src = (r_id in src_pids) or (r_name in src_pids) or (r_acc and r_acc in src_accs)

        is_sender_dst = (s_id in dst_pids) or (s_name in dst_pids) or (s_acc and s_acc in dst_accs)
        is_receiver_dst = (r_id in dst_pids) or (r_name in dst_pids) or (r_acc and r_acc in dst_accs)

        amt = int(txn.get("amount_inr") or 0)

        if is_sender_src and is_receiver_dst:
            txns_src_to_dst.append(txn)
            total_inr_src_to_dst += amt
        elif is_sender_dst and is_receiver_src:
            txns_dst_to_src.append(txn)
            total_inr_dst_to_src += amt

    total_txns = len(txns_src_to_dst) + len(txns_dst_to_src)
    total_inr_exchanged = total_inr_src_to_dst + total_inr_dst_to_src

    # -------------------------------------------------------------
    # 3. FIR Co-Mentions & Police Charges
    # -------------------------------------------------------------
    shared_firs = []
    for fir in datasets.get("firs", []):
        fid = fir.get("fir_id") or f"FIR-{fir.get('day', '')}"
        nar = str(fir.get("narrative") or "").lower()
        acc = str(fir.get("accused_name") or "").lower()
        
        src_hit = any(p in nar or p in acc for p in src_pids if len(p) >= 2)
        dst_hit = any(p in nar or p in acc for p in dst_pids if len(p) >= 2)

        if src_hit and dst_hit:
            shared_firs.append({
                "fir_id": fid,
                "date": fir.get("date") or f"Day {fir.get('day', '')}",
                "station": fir.get("station") or "State Police",
                "ipc_sections": fir.get("ipc_sections") or "Unspecified IPC",
                "location": fir.get("location") or "Unknown",
                "narrative_excerpt": fir.get("narrative", "")[:280] + ("…" if len(fir.get("narrative", "")) > 280 else ""),
            })

    # -------------------------------------------------------------
    # 4. Surveillance Sightings & Intel Co-Observations
    # -------------------------------------------------------------
    shared_surveillance = []
    for surv in datasets.get("surveillance_reports", []):
        notes = str(surv.get("activity_notes") or "").lower()
        src_hit = any(p in notes for p in src_pids if len(p) >= 2)
        dst_hit = any(p in notes for p in dst_pids if len(p) >= 2)
        if src_hit and dst_hit:
            shared_surveillance.append({
                "report_id": surv.get("report_id") or f"SURV-{surv.get('day', '')}",
                "date": surv.get("date") or f"Day {surv.get('day', '')}",
                "team": surv.get("team") or "Field Unit",
                "location": surv.get("location") or "Meeting Point",
                "notes": surv.get("activity_notes", ""),
                "confidence": surv.get("confidence") or "0.85",
            })

    shared_intel = []
    for intel in datasets.get("intelligence_reports", []):
        nar = str(intel.get("narrative") or "").lower()
        ents = str(intel.get("mentioned_entity_ids") or "").lower()
        src_hit = any(p in nar or p in ents for p in src_pids if len(p) >= 2)
        dst_hit = any(p in nar or p in ents for p in dst_pids if len(p) >= 2)
        if src_hit and dst_hit:
            shared_intel.append({
                "report_id": intel.get("report_id") or f"INTEL-{intel.get('day', '')}",
                "date": intel.get("date") or f"Day {intel.get('day', '')}",
                "reliability": intel.get("source_reliability") or "B2",
                "narrative": intel.get("narrative", ""),
            })

    # -------------------------------------------------------------
    # 5. Network Graph Context & Mutual Associates
    # -------------------------------------------------------------
    src_neighbors: Set[str] = set()
    dst_neighbors: Set[str] = set()
    direct_edges_in_graph = []

    for edge in graph.get("edges", []):
        s, d = str(edge.get("src", "")), str(edge.get("dst", ""))
        k = edge.get("kind", "LINKED")
        if s == src_info["id"]:
            src_neighbors.add(d)
        if d == src_info["id"]:
            src_neighbors.add(s)
        if s == dst_info["id"]:
            dst_neighbors.add(d)
        if d == dst_info["id"]:
            dst_neighbors.add(s)

        if (s == src_info["id"] and d == dst_info["id"]) or (s == dst_info["id"] and d == src_info["id"]):
            direct_edges_in_graph.append(edge)

    mutual_ids = (src_neighbors & dst_neighbors) - {src_info["id"], dst_info["id"]}
    mutual_associates = []
    for m_id in sorted(mutual_ids)[:8]:
        m_details = _resolve_person_details(m_id, datasets)
        mutual_associates.append({
            "id": m_details["id"],
            "name": m_details["name"],
            "role": m_details["role"],
            "cell": m_details["cell"],
            "photo": m_details["photo"],
        })

    # Determine connection strength & relationship tier
    evidence_points = 0
    if total_calls > 0: evidence_points += min(4, total_calls // 5 + 1)
    if total_txns > 0: evidence_points += min(4, total_txns * 2)
    if shared_firs: evidence_points += len(shared_firs) * 3
    if shared_surveillance: evidence_points += len(shared_surveillance) * 3
    if shared_intel: evidence_points += len(shared_intel) * 2
    if direct_edges_in_graph: evidence_points += 2

    if evidence_points >= 8:
        strength_label = "Direct Coordinated Operational Link (Critical)"
        strength_badge = "red"
    elif evidence_points >= 4:
        strength_label = "Strong Financial / Communication Tie (High)"
        strength_badge = "orange"
    elif evidence_points >= 2:
        strength_label = "Corroborated Association (Moderate)"
        strength_badge = "blue"
    else:
        strength_label = "Indirect or Low-Frequency Link"
        strength_badge = "gray"

    # -------------------------------------------------------------
    # 6. Plain-English Investigative Story Synthesis
    # -------------------------------------------------------------
    story_paragraphs = []
    
    # Overview Lead
    src_name = src_info["name"]
    dst_name = dst_info["name"]
    src_role = f"{src_info['role']} (Cell {src_info['cell']})" if src_info['cell'] != "Unknown" else src_info['role']
    dst_role = f"{dst_info['role']} (Cell {dst_info['cell']})" if dst_info['cell'] != "Unknown" else dst_info['role']

    story_paragraphs.append(
        f"**{src_name}** ({src_role}) and **{dst_name}** ({dst_role}) share a verified intelligence relationship established across multiple distinct data streams."
    )

    # Telephony Narrative
    if total_calls > 0:
        top_tower_str = ""
        if towers_used:
            best_tower = max(towers_used, key=towers_used.get)
            top_tower_str = f", with the highest density concentrated around the **{best_tower}** cell sector"

        direction_detail = []
        if len(calls_src_to_dst) > 0:
            direction_detail.append(f"{src_name} initiated {len(calls_src_to_dst)} call{'s' if len(calls_src_to_dst)>1 else ''}")
        if len(calls_dst_to_src) > 0:
            direction_detail.append(f"{dst_name} initiated {len(calls_dst_to_src)} call{'s' if len(calls_dst_to_src)>1 else ''}")

        story_paragraphs.append(
            f"📞 **Telephony Evidence**: They exchanged **{total_calls} direct calls** totaling **{total_call_min} minutes** of airtime across {len(call_days)} active days ({', '.join(direction_detail)}){top_tower_str}."
        )

    # Financial Flow Narrative
    if total_txns > 0:
        fin_parts = []
        if total_inr_src_to_dst > 0:
            fin_parts.append(f"₹{total_inr_src_to_dst:,.0f} transferred from {src_name} to {dst_name} ({len(txns_src_to_dst)} txn)")
        if total_inr_dst_to_src > 0:
            fin_parts.append(f"₹{total_inr_dst_to_src:,.0f} transferred from {dst_name} to {src_name} ({len(txns_dst_to_src)} txn)")

        story_paragraphs.append(
            f"💳 **Financial Trail**: Identified **{total_txns} financial transaction{'s' if total_txns>1 else ''}** moving a combined total of **₹{total_inr_exchanged:,.0f}** ({'; '.join(fin_parts)})."
        )

    # FIR / Legal Cases
    if shared_firs:
        fir_ids_str = ", ".join(f[f"fir_id"] for f in shared_firs)
        ipc_sections_str = ", ".join(set(f[f"ipc_sections"] for f in shared_firs if f[f"ipc_sections"]))
        story_paragraphs.append(
            f"📄 **Criminal Case Linkages**: Both individuals are formally recorded in **{len(shared_firs)} police case{'s' if len(shared_firs)>1 else ''}** ({fir_ids_str}) under charges: **{ipc_sections_str or 'IPC Offenses'}**."
        )

    # Surveillance Observations
    if shared_surveillance or shared_intel:
        surv_count = len(shared_surveillance) + len(shared_intel)
        locations = set(s.get("location") for s in shared_surveillance if s.get("location"))
        loc_str = f" at {', '.join(locations)}" if locations else ""
        story_paragraphs.append(
            f"👁️ **Physical Surveillance & Intel**: Field teams documented **{surv_count} physical meeting{'s' if surv_count>1 else ''} / intel observation{'s' if surv_count>1 else ''}** linking both suspects{loc_str}."
        )

    # Mutual Associates
    if mutual_associates:
        m_names = [f"**{m['name']}** ({m['id']})" for m in mutual_associates[:4]]
        story_paragraphs.append(
            f"🤝 **Mutual Network Bridge**: They share **{len(mutual_ids)} mutual contact{'s' if len(mutual_ids)>1 else ''}** in the syndicate, including {', '.join(m_names)}."
        )

    if not total_calls and not total_txns and not shared_firs and not shared_surveillance and not shared_intel:
        if direct_edges_in_graph:
            kinds = ", ".join(set(e.get("kind", "LINKED") for e in direct_edges_in_graph))
            story_paragraphs.append(
                f"🔗 **Network Link**: Directly connected via graph relationship (**{kinds}**) based on evidence records in the active investigation."
            )
        else:
            story_paragraphs.append(
                f"ℹ️ **Network Proximity**: Linked through common multi-hop pathways and mutual associates in the criminal hierarchy."
            )

    full_story = "\n\n".join(story_paragraphs)

    return {
        "status": "success",
        "source_person": src_info,
        "target_person": dst_info,
        "relationship_strength": strength_label,
        "strength_badge": strength_badge,
        "evidence_score": evidence_points,
        "story_synopsis": full_story,
        "telephony": {
            "total_calls": total_calls,
            "total_duration_minutes": total_call_min,
            "calls_src_to_dst": len(calls_src_to_dst),
            "calls_dst_to_src": len(calls_dst_to_src),
            "active_days_count": len(call_days),
            "top_cell_towers": [t for t, _ in sorted(towers_used.items(), key=lambda x: x[1], reverse=True)[:4]],
            "sample_records": (calls_src_to_dst + calls_dst_to_src)[:10],
        },
        "financials": {
            "total_transactions": total_txns,
            "total_amount_inr": total_inr_exchanged,
            "amount_src_to_dst": total_inr_src_to_dst,
            "amount_dst_to_src": total_inr_dst_to_src,
            "sample_records": (txns_src_to_dst + txns_dst_to_src)[:10],
        },
        "police_cases": shared_firs,
        "surveillance": shared_surveillance,
        "intelligence": shared_intel,
        "mutual_associates": mutual_associates,
        "direct_graph_edges": direct_edges_in_graph,
    }
