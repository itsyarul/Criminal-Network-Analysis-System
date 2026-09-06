"""
Geospatial Intelligence & Cell-Tower Movement Engine.

Provides geographic coordinates, call density heatmaps, suspect travel trajectory paths,
and multi-suspect rendezvous meeting hotspots across metropolitan cell sectors.
"""
from typing import Dict, List, Optional, Any, Tuple, Set
from collections import defaultdict
import re
import hashlib

from backend.config import DATA_DIR
from backend.loader import load_all
from backend.graph.builder import load_graph_serial

# Accurate metropolitan coordinates for synthetic case locations (Mumbai/Metro)
LOCATION_COORDINATES: Dict[str, Dict[str, Any]] = {
    "Dockside Ward": {
        "lat": 18.9438, "lng": 72.8423,
        "zone": "South Coast Docks",
        "description": "Port container terminals, cargo bays & transit warehouses",
        "tower_id": "TOW-001"
    },
    "Old Market Circle": {
        "lat": 18.9512, "lng": 72.8315,
        "zone": "Central Commercial",
        "description": "High-density wholesale market, cash exchange & hawala hub",
        "tower_id": "TOW-002"
    },
    "Riverside Colony": {
        "lat": 19.0178, "lng": 72.8478,
        "zone": "Riverfront Sector",
        "description": "Residential cluster near transit canal & safehouse hideouts",
        "tower_id": "TOW-003"
    },
    "Industrial Estate Road": {
        "lat": 19.0024, "lng": 72.8310,
        "zone": "Industrial Zone",
        "description": "Automated workshops, printing presses & logistics parks",
        "tower_id": "TOW-004"
    },
    "Central Junction": {
        "lat": 19.0760, "lng": 72.8777,
        "zone": "Transit Hub",
        "description": "Major railway junction & interstate highway intersection",
        "tower_id": "TOW-005"
    },
    "Eastgate": {
        "lat": 19.0596, "lng": 72.8295,
        "zone": "Coastal Gateway",
        "description": "Commercial avenue with high vehicular traffic & banking branches",
        "tower_id": "TOW-006"
    },
    "Hilltop Society": {
        "lat": 19.0688, "lng": 72.8350,
        "zone": "Uptown Sector",
        "description": "Upscale residential enclave overlooking western coast",
        "tower_id": "TOW-007"
    },
    "Station Road": {
        "lat": 18.9700, "lng": 72.8180,
        "zone": "Terminal Sector",
        "description": "Central commuter station, taxi ranks & payphone clusters",
        "tower_id": "TOW-008"
    },
    "New Colony": {
        "lat": 19.1136, "lng": 72.8697,
        "zone": "North Industrial",
        "description": "Mixed commercial offices & telecom switching centers",
        "tower_id": "TOW-009"
    },
    "Warehouse District": {
        "lat": 19.0330, "lng": 72.8600,
        "zone": "Freight Yards",
        "description": "Bonded storage yards, container storage & courier depots",
        "tower_id": "TOW-010"
    },
    "North Bypass": {
        "lat": 19.1726, "lng": 72.8566,
        "zone": "Highway Arterial",
        "description": "Expressway toll plaza, truck stops & perimeter checkpoints",
        "tower_id": "TOW-011"
    },
    "Lakeview Chowk": {
        "lat": 19.1250, "lng": 72.9050,
        "zone": "Eastern Periphery",
        "description": "Perimeter crossroads near recreational lake & quiet zones",
        "tower_id": "TOW-012"
    },
}

DEFAULT_CENTER = {"lat": 19.0450, "lng": 72.8550, "zoom": 12}


def _get_loc_coords(loc_name: str) -> Dict[str, Any]:
    for name, data in LOCATION_COORDINATES.items():
        if name.lower() in loc_name.lower() or loc_name.lower() in name.lower():
            return data
    # Fallback to hash-deterministic offset near center
    h = int(hashlib.md5(loc_name.encode()).hexdigest()[:6], 16)
    d_lat = ((h % 1000) / 1000.0 - 0.5) * 0.15
    d_lng = (((h // 1000) % 1000) / 1000.0 - 0.5) * 0.15
    return {
        "lat": round(DEFAULT_CENTER["lat"] + d_lat, 4),
        "lng": round(DEFAULT_CENTER["lng"] + d_lng, 4),
        "zone": "Peripheral Zone",
        "description": f"Extrapolated sector for {loc_name}",
        "tower_id": f"TOW-{abs(h)%900+100}"
    }


import hashlib


def get_cell_towers_geospatial(datasets: Optional[Dict] = None) -> Dict[str, Any]:
    """
    Compile rich geospatial data for all cell towers:
    - Latitude/longitude coordinates
    - Call volume intensity & duration
    - Unique suspects active
    - Cell group distribution (Cell A, B, C, Bridge)
    """
    if datasets is None:
        datasets, _ = load_all(DATA_DIR)

    pd = datasets.get("people_directory", {})
    person_to_cell = {p["id"]: p.get("cell", "Unknown") for p in pd.get("network_people", []) + pd.get("noise_people", [])}

    tower_stats = defaultdict(lambda: {
        "call_count": 0,
        "total_duration_sec": 0,
        "callers": set(),
        "callees": set(),
        "cell_counts": defaultdict(int),
        "days_active": set(),
    })

    for cdr in datasets.get("cdrs", []):
        loc = cdr.get("cell_tower_location") or "Unknown"
        dur = int(cdr.get("duration_sec") or 0)
        c1 = str(cdr.get("caller_id") or "")
        c2 = str(cdr.get("callee_id") or "")
        day = cdr.get("day")

        stats = tower_stats[loc]
        stats["call_count"] += 1
        stats["total_duration_sec"] += dur
        if c1: stats["callers"].add(c1)
        if c2: stats["callees"].add(c2)
        if day: stats["days_active"].add(day)

        cell1 = person_to_cell.get(c1, "Unknown")
        cell2 = person_to_cell.get(c2, "Unknown")
        if cell1 in ("A", "B", "C"): stats["cell_counts"][cell1] += 1
        if cell2 in ("A", "B", "C"): stats["cell_counts"][cell2] += 1

    towers_list = []
    max_calls = max((s["call_count"] for s in tower_stats.values()), default=1)

    for loc, stats in tower_stats.items():
        coords = _get_loc_coords(loc)
        active_suspects = list(stats["callers"] | stats["callees"])
        dominant_cell = max(stats["cell_counts"], key=stats["cell_counts"].get) if stats["cell_counts"] else "Unknown"

        intensity = round(stats["call_count"] / max_calls, 3)

        towers_list.append({
            "name": loc,
            "tower_name": loc,
            "tower_id": coords.get("tower_id", f"TOW-{len(towers_list)+1:03d}"),
            "lat": coords["lat"],
            "lng": coords["lng"],
            "zone": coords.get("zone", "Metropolitan Area"),
            "description": coords.get("description", ""),
            "call_count": stats["call_count"],
            "total_duration_minutes": round(stats["total_duration_sec"] / 60.0, 1),
            "unique_suspects_count": len(active_suspects),
            "unique_suspects": active_suspects[:10],
            "dominant_cell": dominant_cell,
            "cell_distribution": dict(stats["cell_counts"]),
            "is_cross_cell": len([c for c in stats["cell_counts"] if stats["cell_counts"][c] >= 3]) >= 2,
            "intensity": intensity,
            "days_active_count": len(stats["days_active"]),
        })

    towers_list.sort(key=lambda t: t["call_count"], reverse=True)

    return {
        "status": "success",
        "center": DEFAULT_CENTER,
        "total_towers": len(towers_list),
        "towers": towers_list,
    }


def get_suspect_trajectories(
    datasets: Optional[Dict] = None,
    person_id: Optional[str] = None,
    day_start: Optional[int] = None,
    day_end: Optional[int] = None
) -> Dict[str, Any]:
    """
    Reconstruct suspect travel trajectories over time across towers,
    surveillance sightings, and crime scenes.
    """
    if datasets is None:
        datasets, _ = load_all(DATA_DIR)

    pd = datasets.get("people_directory", {})
    all_people = pd.get("network_people", []) + pd.get("noise_people", [])
    person_map = {p["id"]: p for p in all_people}

    target_pids = [person_id] if person_id else [p["id"] for p in all_people if p.get("cell") in ("A", "B", "C", "Bridge")]

    trajectories_by_person = defaultdict(list)

    # 1. From CDRs
    for cdr in datasets.get("cdrs", []):
        day = cdr.get("day")
        if day is None:
            continue
        try:
            day_int = int(day)
        except ValueError:
            continue
        if day_start is not None and day_int < day_start:
            continue
        if day_end is not None and day_int > day_end:
            continue

        c1, c2 = str(cdr.get("caller_id") or ""), str(cdr.get("callee_id") or "")
        loc = cdr.get("cell_tower_location") or "Unknown"
        coords = _get_loc_coords(loc)
        ts = cdr.get("timestamp") or f"Day {day_int}"

        for pid, role in [(c1, "caller"), (c2, "callee")]:
            if pid in target_pids:
                trajectories_by_person[pid].append({
                    "day": day_int,
                    "timestamp": ts,
                    "location_name": loc,
                    "lat": coords["lat"],
                    "lng": coords["lng"],
                    "event_type": "CDR_CALL",
                    "details": f"Call as {role} ({cdr.get('duration_sec', 0)}s) via tower {loc}",
                })

    # 2. From Surveillance Reports
    for surv in datasets.get("surveillance_reports", []):
        day = surv.get("day")
        loc = surv.get("location") or "Unknown"
        coords = _get_loc_coords(loc)
        notes = surv.get("activity_notes", "")
        for p in all_people:
            pid = p["id"]
            if (pid in target_pids) and (pid.lower() in notes.lower() or p.get("name", "").lower() in notes.lower()):
                trajectories_by_person[pid].append({
                    "day": int(day) if str(day).isdigit() else 1,
                    "timestamp": surv.get("date") or f"Day {day}",
                    "location_name": loc,
                    "lat": coords["lat"],
                    "lng": coords["lng"],
                    "event_type": "SURVEILLANCE_SIGHTING",
                    "details": f"Physical sighting: {notes[:100]}",
                })

    result_trajectories = []
    for pid, points in trajectories_by_person.items():
        if not points:
            continue
        # Sort chronologically by day
        points.sort(key=lambda x: (x["day"], x["timestamp"]))
        p_info = person_map.get(pid, {"id": pid, "name": pid, "role": "Suspect", "cell": "Unknown"})

        result_trajectories.append({
            "person_id": pid,
            "person_name": p_info.get("name", pid),
            "role": p_info.get("role", "Suspect"),
            "cell": p_info.get("cell", "Unknown"),
            "color": "#4C9AFF" if p_info.get("cell") == "A" else "#AB68FF" if p_info.get("cell") == "B" else "#FF7A45" if p_info.get("cell") == "C" else "#FFC53D",
            "waypoints_count": len(points),
            "path_coordinates": [[pt["lat"], pt["lng"]] for pt in points],
            "timeline_events": points,
        })

    result_trajectories.sort(key=lambda t: t["waypoints_count"], reverse=True)

    return {
        "status": "success",
        "total_tracked_suspects": len(result_trajectories),
        "trajectories": result_trajectories,
    }


def get_co_location_hotspots(datasets: Optional[Dict] = None) -> Dict[str, Any]:
    """
    Identify meeting hotspots where multiple suspects converged.
    """
    if datasets is None:
        datasets, _ = load_all(DATA_DIR)

    pd = datasets.get("people_directory", {})
    all_people = pd.get("network_people", []) + pd.get("noise_people", [])
    person_map = {p["id"]: p for p in all_people}

    location_events = defaultdict(lambda: {
        "suspects": set(),
        "cells": set(),
        "days": set(),
        "total_interactions": 0,
        "sample_evidence": [],
    })

    for cdr in datasets.get("cdrs", []):
        loc = cdr.get("cell_tower_location") or "Unknown"
        c1, c2 = str(cdr.get("caller_id") or ""), str(cdr.get("callee_id") or "")
        day = cdr.get("day")
        if c1:
            location_events[loc]["suspects"].add(c1)
            cell = person_map.get(c1, {}).get("cell")
            if cell: location_events[loc]["cells"].add(cell)
        if c2:
            location_events[loc]["suspects"].add(c2)
            cell = person_map.get(c2, {}).get("cell")
            if cell: location_events[loc]["cells"].add(cell)
        if day: location_events[loc]["days"].add(day)
        location_events[loc]["total_interactions"] += 1

    for surv in datasets.get("surveillance_reports", []):
        loc = surv.get("location") or "Unknown"
        notes = surv.get("activity_notes", "")
        for p in all_people:
            if p["id"].lower() in notes.lower() or p.get("name", "").lower() in notes.lower():
                location_events[loc]["suspects"].add(p["id"])
                if p.get("cell"): location_events[loc]["cells"].add(p.get("cell"))
        location_events[loc]["total_interactions"] += 2
        location_events[loc]["sample_evidence"].append(surv.get("activity_notes", "")[:120])

    hotspots = []
    for loc, data in location_events.items():
        distinct_cells = [c for c in data["cells"] if c in ("A", "B", "C", "Bridge")]
        if len(data["suspects"]) >= 3 and len(distinct_cells) >= 2:
            coords = _get_loc_coords(loc)
            hotspots.append({
                "location_name": loc,
                "lat": coords["lat"],
                "lng": coords["lng"],
                "zone": coords.get("zone", "Metropolitan Area"),
                "description": coords.get("description", ""),
                "suspects_count": len(data["suspects"]),
                "suspects_list": list(data["suspects"])[:8],
                "cells_involved": distinct_cells,
                "days_count": len(data["days"]),
                "total_events": data["total_interactions"],
                "risk_tier": "CRITICAL MEETING HUB" if len(distinct_cells) >= 3 else "HIGH CO-LOCATION SITE",
                "sample_evidence": data["sample_evidence"][:2],
            })

    hotspots.sort(key=lambda h: (len(h["cells_involved"]), h["suspects_count"]), reverse=True)

    return {
        "status": "success",
        "total_hotspots": len(hotspots),
        "hotspots": hotspots,
    }
