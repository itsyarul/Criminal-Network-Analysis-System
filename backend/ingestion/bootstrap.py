"""
Autonomous Entity Bootstrapper (Pillar 3.E).

Auto-creates suspect profiles from distinct phone numbers, bank accounts
and caller/callee names when no pre-existing people_directory.json rows
cover them — so arbitrary messy uploads still build a graph.
"""
from typing import Dict, List, Tuple
import re


def _phones_from_datasets(datasets: Dict) -> Dict[str, Dict]:
    found: Dict[str, Dict] = {}
    for r in datasets.get("cdrs", []) or []:
        for phone_key, id_key, name_key in [
            ("caller_phone", "caller_id", "caller_name"),
            ("callee_phone", "callee_id", "callee_name"),
        ]:
            phone = str(r.get(phone_key) or "").strip()
            if not phone or phone.lower() == "unknown":
                continue
            entry = found.setdefault(phone, {"phone": phone, "ids": set(), "names": set()})
            if r.get(id_key):
                entry["ids"].add(str(r[id_key]))
            if r.get(name_key):
                entry["names"].add(str(r[name_key]))
    return found


def _accounts_from_datasets(datasets: Dict) -> Dict[str, Dict]:
    found: Dict[str, Dict] = {}
    for r in datasets.get("transactions", []) or []:
        for acc_key, id_key, name_key in [
            ("sender_account", "sender_id", "sender_name"),
            ("receiver_account", "receiver_id", "receiver_name"),
        ]:
            acc = str(r.get(acc_key) or "").strip()
            if not acc:
                continue
            entry = found.setdefault(acc, {"account": acc, "ids": set(), "names": set()})
            if r.get(id_key):
                entry["ids"].add(str(r[id_key]))
            if r.get(name_key):
                entry["names"].add(str(r[name_key]))
    # criminal_history known addresses do not yield accounts; skip
    return found


def bootstrap_entities(datasets: Dict, people_directory: Dict) -> Tuple[Dict, Dict]:
    """Ensure every phone/account/name seen in CDRs/transactions has a profile.

    Returns (updated_people_directory, stats{phones_bootstrapped, accounts_bootstrapped, ...}).
    Idempotent: never duplicates an existing phone/account/id.
    """
    pd = {
        "network_people": list((people_directory or {}).get("network_people", [])),
        "noise_people": list((people_directory or {}).get("noise_people", [])),
    }
    known_phones = {str(p.get("phone")) for p in pd["network_people"] + pd["noise_people"] if p.get("phone")}
    known_accounts = {str(p.get("account")) for p in pd["network_people"] + pd["noise_people"] if p.get("account")}
    known_ids = {str(p.get("id")) for p in pd["network_people"] + pd["noise_people"] if p.get("id")}

    stats = {"phones_bootstrapped": 0, "accounts_bootstrapped": 0, "names_bootstrapped": 0}
    auto_n = 1

    def _next_auto_id() -> str:
        nonlocal auto_n
        while f"AUTO-{auto_n:03d}" in known_ids:
            auto_n += 1
        nid = f"AUTO-{auto_n:03d}"
        auto_n += 1
        known_ids.add(nid)
        return nid

    for phone, info in _phones_from_datasets(datasets).items():
        if phone in known_phones:
            continue
        # prefer a real observed id/name over a synthetic one
        existing_ids = [i for i in info["ids"] if i not in known_ids and not i.startswith("UNK")]
        name = sorted(info["names"])[0] if info["names"] else ""
        nid = existing_ids[0] if existing_ids else _next_auto_id()
        known_ids.add(nid)
        pd["network_people"].append({
            "id": nid,
            "name": name or nid,
            "role": "Unknown (auto-bootstrapped)",
            "cell": "Unknown",
            "phone": phone,
            "account": "",
        })
        known_phones.add(phone)
        stats["phones_bootstrapped"] += 1

    for acc, info in _accounts_from_datasets(datasets).items():
        if acc in known_accounts:
            continue
        # attach to the phone-bootstrapped profile with the same id if possible
        target = None
        for cand_id in info["ids"]:
            target = next((p for p in pd["network_people"] if p["id"] == cand_id), None)
            if target is not None:
                break
        if target is not None and not target.get("account"):
            target["account"] = acc
            known_accounts.add(acc)
            continue
        existing_ids = [i for i in info["ids"] if i not in known_ids and not i.startswith("UNK")]
        name = sorted(info["names"])[0] if info["names"] else ""
        nid = existing_ids[0] if existing_ids else _next_auto_id()
        known_ids.add(nid)
        pd["network_people"].append({
            "id": nid,
            "name": name or nid,
            "role": "Unknown (auto-bootstrapped)",
            "cell": "Unknown",
            "phone": "",
            "account": acc,
        })
        known_accounts.add(acc)
        stats["accounts_bootstrapped"] += 1

    # names seen only in FIR narratives cannot be reliably keyed — only
    # bootstrap bare names from structured caller/callee slots that lack both
    # phone and account (rare, but keeps the graph connected).
    for r in datasets.get("cdrs", []) or []:
        for name_key in ("caller_name", "callee_name"):
            nm = str(r.get(name_key) or "").strip()
            if not nm:
                continue
            if any(p.get("name") == nm for p in pd["network_people"] + pd["noise_people"]):
                continue
            nid = _next_auto_id()
            pd["network_people"].append({
                "id": nid, "name": nm, "role": "Unknown (auto-bootstrapped)",
                "cell": "Unknown", "phone": "", "account": "",
            })
            stats["names_bootstrapped"] += 1
            if stats["names_bootstrapped"] > 200:
                break

    return pd, stats
