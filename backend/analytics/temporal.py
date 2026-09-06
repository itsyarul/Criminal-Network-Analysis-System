"""
Temporal Intelligence (Task 3 Feature 4).

Explain why activities on Days 58/61/64 are considered correlated, derived from data
(not hardcoded). Uses burst detection window analysis.
"""
from typing import Dict, List
from collections import defaultdict

from backend.config import DATA_DIR
from backend.loader import load_all
from backend.analytics.burst_detection import detect_bursts, daily_counts_by_cell

def get_temporal_intelligence(datasets: Dict = None) -> Dict:
    if datasets is None:
        datasets, _ = load_all(DATA_DIR)
    bursts = detect_bursts(datasets)
    # Group bursts by day proximity (7-day span = correlated per spec)
    # Correct: group days where max-min <=7 (not just anchor distance)
    sorted_b = sorted(bursts, key=lambda x: x["day"])
    groups = []
    used = set()
    for b in sorted_b:
        if b["day"] in used:
            continue
        # Collect bursts where each day is within 7 of *all* group members (max-min <=7)
        # Start with anchor, expand while span <=7
        group = [b]
        for other in sorted_b:
            if other["day"] == b["day"]:
                continue
            candidate_days = sorted(set(x["day"] for x in group + [other]))
            if max(candidate_days) - min(candidate_days) <= 7 and abs(other["day"] - b["day"]) <= 7:
                group.append(other)
        if len(set(x["cell"] for x in group)) >= 2:
            cells = sorted(set(x["cell"] for x in group))
            days = sorted(set(x["day"] for x in group))
            span = max(days) - min(days)
            max_z = max(x["zscore"] for x in group)
            groups.append({
                "span": [min(days), max(days)],
                "days": days,
                "cells": cells,
                "burst_count": len(group),
                "max_zscore": max_z,
                "explanation": f"Correlated burst: {len(cells)} cells ({', '.join(cells)}) exceeded z>2.0 within {span}-day span {days}, max z={max_z}. Each burst derived as (count - mean[day-6..day-1])/std.",
                "supporting_bursts": group,
                "evidence_hash": f"temporal-{min(days)}-{max(days)}"
            })
            for x in group:
                used.add(x["day"])
    # Story slice 50-70 specific highlight
    story_bursts = [b for b in bursts if 50 <= b["day"] <= 70]
    story_groups = [g for g in groups if any(50 <= d <= 70 for d in g["days"])]
    # Derive Day 58/61/64 narrative from actual closest bursts
    narrative = []
    for target in [58, 61, 64]:
        closest = min(bursts, key=lambda x: abs(x["day"]-target)) if bursts else None
        if closest and abs(closest["day"]-target) <= 3:
            narrative.append({"target_day": target, "actual_burst": closest, "offset": closest["day"]-target})
        else:
            narrative.append({"target_day": target, "actual_burst": None, "offset": None})

    return {
        "bursts": bursts,
        "correlated_groups": groups,
        "story_slice": {"range": [50,70], "bursts_in_slice": story_bursts, "correlated_in_slice": story_groups},
        "narrative_days": narrative,
        "explanation": f"Derived from {len(bursts)} bursts (z>2.0) across A/B/C. {len(groups)} correlated groups where ≥2 cells spiked within 7 days. Story slice 50-70 contains {len(story_bursts)} bursts."
    }

def temporal_for_entity(entity_id: str, datasets: Dict = None) -> Dict:
    if datasets is None:
        datasets, _ = load_all(DATA_DIR)
    intel = get_temporal_intelligence(datasets)
    # Map entity to its cell, then find its cell's bursts
    pd = datasets.get("people_directory", {})
    id_to_cell = {p["id"]: p.get("cell") for p in pd.get("network_people", [])+pd.get("noise_people",[])}
    cell = id_to_cell.get(entity_id)
    if not cell:
        return {"correlated": False, "reason": "Unknown cell"}
    cell_bursts = [b for b in intel["bursts"] if b["cell"]==cell]
    correlated = any(cell in g["cells"] for g in intel["correlated_groups"])
    return {
        "entity_id": entity_id,
        "cell": cell,
        "cell_bursts": cell_bursts[:3],
        "correlated": correlated,
        "explanation": f"Entity {entity_id} cell {cell} has {len(cell_bursts)} bursts; correlated={correlated} (within 7-day multi-cell span)" if cell_bursts else "No bursts for entity cell"
    }
