"""
Burst / correlated burst detection per criminal-network-live-reveal.md:76

Burst-sync: z = (count[d] - mean[d-6..d-1]) / std[d-6..d-1];
  if std==0: z=0 unless count>2*mean then z=3; flag if z>2.0;
  correlated burst = ≥2 cells flag within 7-day span (covers 58/61/64).
"""
import math
from typing import Dict, List
from collections import defaultdict, Counter

def daily_counts_by_cell(datasets: Dict) -> Dict[str, Dict[int,int]]:
    """Count CDR calls per day per cell using caller/callee cell lookup."""
    # Build id -> cell
    pd = datasets.get("people_directory", {})
    id_to_cell = {p["id"]: p.get("cell") for p in pd.get("network_people",[]) + pd.get("noise_people",[])}
    counts = defaultdict(Counter)  # cell -> day->count
    for row in datasets.get("cdrs", []):
        # Credit burst to caller cell (or dominant)
        cid = row.get("caller_id")
        cell = id_to_cell.get(cid, "Noise")
        day = row.get("day")
        try:
            day = int(day)
        except:
            continue
        # only count network cells A/B/C for burst (Noise excluded for main detectors)
        counts[cell][day] += 1
    # Fuse transactions per sender cell as an additional temporal signal
    # (design: the correlated burst week is only visible once CDR +
    # financial signals are fused). CDR stays primary — one transaction
    # counts as one event, the same scale as one call, so the z>2.0
    # threshold behavior is preserved.
    for row in datasets.get("transactions", []):
        sid = row.get("sender_id")
        cell = id_to_cell.get(sid, "Noise")
        day = row.get("day")
        try:
            day = int(day)
        except:
            continue
        counts[cell][day] += 1
    return counts

def zscore_for_day(day: int, counts: Dict[int,int]) -> float:
    window = [counts.get(d,0) for d in range(day-6, day)]
    if len(window) < 6:
        return 0.0
    mean = sum(window)/len(window)
    # std
    var = sum((x-mean)**2 for x in window)/len(window)
    std = math.sqrt(var)
    curr = counts.get(day,0)
    if std == 0:
        if mean == 0:
            return 0.0
        return 3.0 if curr > 2*mean else 0.0
    return (curr - mean)/std

def detect_bursts(datasets: Dict) -> List[Dict]:
    counts_by_cell = daily_counts_by_cell(datasets)
    bursts = []
    for cell in ("A","B","C"):
        counts = counts_by_cell.get(cell, {})
        for day in range(1,91):
            z = zscore_for_day(day, counts)
            if z > 2.0:
                bursts.append({"cell": cell, "day": day, "zscore": round(z,2), "count": counts.get(day,0),
                               "window": [day-6, day-1], "mean": round(sum(counts.get(d,0) for d in range(day-6,day))/6,2)})
    # Correlated bursts: ≥2 cells flag within 7-day span
    correlated = []
    bursts_sorted = sorted(bursts, key=lambda x: x["day"])
    for i, b in enumerate(bursts_sorted):
        span = [x for x in bursts_sorted if abs(x["day"]-b["day"]) <= 7]
        cells = set(x["cell"] for x in span)
        if len(cells) >= 2:
            correlated.append({"anchor_day": b["day"], "cells": sorted(cells), "burst_days": sorted(set(x["day"] for x in span))})
    # Deduplicate correlated groups by burst_days frozenset
    seen = set()
    uniq_corr = []
    for c in correlated:
        key = tuple(c["burst_days"])
        if key not in seen:
            seen.add(key)
            uniq_corr.append(c)
    # Surface the "N cells synchronized" story on the bursts themselves.
    # Additive fields only — the existing cell/day/zscore/count/window/mean
    # contract is unchanged, so all current callers keep working. A burst is
    # correlated when its day falls in a group spanning ≥2 cells within the
    # 7-day window.
    day_to_group = {}
    for c in uniq_corr:
        for d in c["burst_days"]:
            day_to_group.setdefault(d, c)
    for b in bursts:
        group = day_to_group.get(b["day"])
        if group is not None:
            b["correlated"] = True
            b["correlated_cells"] = group["cells"]
            b["correlated_days"] = group["burst_days"]
        else:
            b["correlated"] = False
            b["correlated_cells"] = [b["cell"]]
            b["correlated_days"] = [b["day"]]
    return bursts

def detect_correlated_bursts(bursts: List[Dict]) -> List[Dict]:
    """Group bursts where ≥2 cells within 7 days."""
    groups = []
    for b in sorted(bursts, key=lambda x: x["day"]):
        # find neighbours within 7
        neigh = [x for x in bursts if abs(x["day"]-b["day"]) <= 7]
        cells = set(x["cell"] for x in neigh)
        if len(cells) >= 2:
            groups.append({"span": [min(x["day"] for x in neigh), max(x["day"] for x in neigh)], "cells": sorted(cells), "days": sorted(set(x["day"] for x in neigh)), "zscores": {f"{x['cell']}:{x['day']}": x["zscore"] for x in neigh}})
    # dedupe
    uniq = []
    seen = set()
    for g in groups:
        k = tuple(g["days"])
        if k not in seen:
            seen.add(k)
            uniq.append(g)
    return uniq
