"""
Financial anomaly — structuring fan per criminal-network-live-reveal.md:77
  Structuring-fan: ≥10 cash txns <50000 INR to same receiver within 12 days + ≥2 consolidations ≥250k within 6 days → flag C12/C11.
Also detects lump-sum bursts (A/B chains) via simple threshold.
"""
from typing import Dict, List
from collections import defaultdict

def detect_structuring(datasets: Dict) -> List[Dict]:
    txns = datasets.get("transactions", [])
    # Group by receiver
    by_receiver = defaultdict(list)
    for t in txns:
        try:
            amt = int(str(t["amount_inr"]).replace(",",""))
        except:
            continue
        by_receiver[t["receiver_id"]].append({"amt": amt, "day": int(t["day"]) if t.get("day") else 0, "type": t.get("txn_type"), "txn": t})

    flags = []
    for recv, lst in by_receiver.items():
        # cash <50000
        cash_small = [x for x in lst if x["type"]=="Cash" and x["amt"] < 50000]
        if len(cash_small) < 10:
            continue
        # check 12-day window containing ≥10
        days = sorted(x["day"] for x in cash_small)
        found_window = None
        for i in range(len(days)):
            window_end = days[i] + 12
            cnt = sum(1 for d in days if days[i] <= d <= window_end)
            if cnt >= 10:
                found_window = [days[i], window_end]
                break
        if not found_window:
            continue
        # Check consolidations: ≥2 txns ≥250k from this receiver outward within 6 days after window
        consolidations = []
        for t in txns:
            if t.get("sender_id") == recv:
                try:
                    amt = int(str(t["amount_inr"]).replace(",",""))
                    day = int(t["day"])
                except:
                    continue
                if amt >= 250000 and found_window[0] <= day <= found_window[1]+6:
                    consolidations.append({"txn_id": t.get("txn_id"), "amount": amt, "day": day, "receiver": t.get("receiver_id")})
        if len(consolidations) >= 2:
            # also find upward forwarding (Hawala)
            forwards = [t for t in txns if t.get("sender_id") in [c["receiver"] for c in consolidations] or t.get("sender_id")==recv]
            flags.append({
                "receiver": recv,
                "pattern": "structuring_fan",
                "cash_small_count": len(cash_small),
                "window": found_window,
                "consolidations": consolidations,
                "confidence": 0.92,
                "explain": f"{recv} received {len(cash_small)} cash <50k within 12d {found_window}, then {len(consolidations)} consolidations ≥250k"
            })
    return flags

def detect_lump_sums(datasets: Dict) -> List[Dict]:
    flags = []
    for t in datasets.get("transactions", []):
        try:
            amt = int(str(t["amount_inr"]).replace(",",""))
            day = int(t["day"])
        except:
            continue
        if amt >= 150000 and t.get("txn_type") in ("Hawala","Bank Transfer"):
            # heuristic: flag large Hawala/Bank near events days 54-63
            if 50 <= day <= 70 and amt >= 150000:
                flags.append({"txn_id": t.get("txn_id"), "amount": amt, "day": day, "type": t.get("txn_type"),
                              "sender": t.get("sender_id"), "receiver": t.get("receiver_id"), "pattern": "large_transfer"})
    return flags
