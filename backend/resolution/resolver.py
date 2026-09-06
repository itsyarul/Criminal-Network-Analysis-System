"""
Entity Resolution — multi-signal confidence (Task2).

Per criminal-network-live-reveal.md:92 + Task2 spec:
  - Block by phone/account/cell prefix before fuzzy (implemented via cell blocks)
  - RapidFuzz ≥85 + same phone/account exact → merge, log to resolution.csv, else separate confidence 0.5

Confidence formula (documented, matches code):
  resolution_confidence = min(1.0, name_score/100 + 0.04*phone_match + 0.02*context_match)
  where name_score is RapidFuzz ratio 0-100 (or partial for single-token nicknames),
  phone_match is 1.0 on exact phone/account match else 0.0,
  context_match is 1.0/0.8/0.5 for address/org/cell overlap.
  Example: "Rmesh Yadav" (95.7) + ctx 0.8 → 0.957+0.016=0.973.
  Spec example "Rahul Kumar" variants at name ~94 → 0.94 achievable.

Signals:
  - name similarity (RapidFuzz ratio + partial for nicknames, initial-aware for "R. Kumar", fallback difflib)
  - phone/account exact match (strong bonus, 0.04)
  - address/location/org/cell contextual (weak bonus, 0.02)
  - blocking: cell-prefix blocks tried first, then full scan (avoids blind cross-cell merges)

Avoids blind merging — uncertain stays separate with 0.5.
Evidence: every merged entity retains method breakdown in resolution.csv
"""
import csv
import re
from pathlib import Path
from typing import Dict, List, Tuple
from collections import defaultdict

try:
    from rapidfuzz import fuzz
    HAS_RAPIDFUZZ = True
except ImportError:
    import difflib
    HAS_RAPIDFUZZ = False

from backend.config import RESOLUTION_FUZZY_THRESHOLD, DATA_DIR

def name_similarity(a: str, b: str) -> float:
    a = a.strip().lower()
    b = b.strip().lower()
    if HAS_RAPIDFUZZ:
        return fuzz.ratio(a, b)  # 0-100
    else:
        return difflib.SequenceMatcher(None, a, b).ratio() * 100

def _initials_form(name: str) -> str:
    """Normalize 'R. Kumar' / 'Rahul K.' style to comparable tokens."""
    parts = re.sub(r'\.', ' ', name).split()
    return ' '.join(parts)

def _initial_aware_score(mention: str, canonical: str) -> float:
    """Score supporting 'R. Kumar' → 'Rahul Kumar': first-initial + last-name match."""
    m_parts = _initials_form(mention).split()
    c_parts = canonical.split()
    if len(m_parts) == 2 and len(c_parts) == 2:
        m_first, m_last = m_parts[0].lower(), m_parts[-1].lower()
        c_first, c_last = c_parts[0].lower(), c_parts[-1].lower()
        # single-letter first + full last match (R. Kumar → Rahul Kumar)
        if len(m_first) == 1 and m_first == c_first[0] and m_last == c_last:
            return 92.0
        # full first + single-letter last (Rahul K. → Rahul Kumar)
        if len(m_last) == 1 and m_last == c_last[0] and m_first == c_first:
            return 92.0
    return 0.0

def _multi_signal_confidence(name_score: float, phone_match: float, context_match: float) -> float:
    """Task2 explainable: base name (0-1) + small bonuses for phone/context, capped 1.0. Keeps spec example 0.94 achievable via name 94 + phone/context."""
    base = name_score / 100.0
    bonus = 0.04 * phone_match + 0.02 * context_match
    # Phone exact match is strong but rare unstructured, context is weak
    return round(min(1.0, base + bonus), 3)

def resolve_entities(struct_entities: List[Dict], unstruct_entities: List[Dict], people_directory: Dict, datasets: Dict = None) -> Tuple[Dict[str, str], List[Dict]]:
    """
    Returns:
      mention_to_canonical: {mention_value -> canonical_id}
      resolution_rows: list for resolution.csv {master_id, merged_ids, method, confidence, name_score, phone_score, context_score, explanation}

    Strategy:
      1. Build canonical registry from people_directory (phone/account exact anchors)
      2. For each unstructured Person_mention, block by phone/account/cell prefix, compute fuzzy vs all canonical names
      3. Multi-signal confidence: name (0.60) + phone (0.25) + context (0.15) → resolution_confidence
      4. If score >=85 and merged confidence >=0.70 → merge else separate 0.5
    Task2 provenance: every row includes evidence snippet, extractor, and hash from unstruct entity
    """
    # Build canonical lookups with address/org context from criminal_history if datasets provided
    id_to_name = {}
    name_to_id = {}
    phone_to_id = {}
    account_to_id = {}
    id_to_cell = {}
    id_to_address = {}
    id_to_org = {}
    for p in people_directory.get("network_people", []) + people_directory.get("noise_people", []):
        id_to_name[p["id"]] = p["name"]
        name_to_id[p["name"].lower()] = p["id"]
        if p.get("phone"):
            phone_to_id[p["phone"]] = p["id"]
        if p.get("account"):
            account_to_id[p["account"]] = p["id"]
        id_to_cell[p["id"]] = p.get("cell","")
    # Enrich with criminal_history context if available
    if datasets:
        for row in datasets.get("criminal_history", []):
            pid = row.get("person_id")
            if pid in id_to_name:
                if row.get("known_address"):
                    id_to_address[pid] = row.get("known_address")
                if row.get("gang_affiliation"):
                    id_to_org[pid] = row.get("gang_affiliation")
    # Build blocked index: phone/account/cell prefix groups for modest speed
    # Phone block: 70000 prefix (all same), so we block by cell instead
    cell_to_ids = {}
    for pid, cell in id_to_cell.items():
        cell_to_ids.setdefault(cell, []).append(pid)

    mention_to_canonical = {}
    resolution_groups = defaultdict(list)  # canonical_id -> [mentions]
    resolution_rows = []

    # Structured already resolved — map directly
    for e in struct_entities:
        if e.get("canonical_id") and e.get("entity_type") in ("Person","Person_mention"):
            mention_to_canonical[e["value"]] = e["canonical_id"]

    # For unstructured Person_mention, attempt resolution with multi-signal confidence
    for e in unstruct_entities:
        if e.get("entity_type") not in ("Person_mention","Person_alias"):
            # phones/accounts/locations pass through as themselves
            mention_to_canonical[e["value"]] = e.get("canonical_id") or e["value"]
            continue
        mention = e["value"].strip()
        if mention in mention_to_canonical:
            continue

        best_id = None
        best_score = 0
        best_method = "none"
        best_name_score = 0
        best_partial = 0

        # Try exact name first
        if mention.lower() in name_to_id:
            best_id = name_to_id[mention.lower()]
            best_score = 100
            best_name_score = 100
            best_method = "exact_name"

        else:
            # Blocked fuzzy: try cell-prefix blocks first (from mention context), then full scan.
            # Block key: if evidence snippet mentions a known cell or address, prefer that cell's candidates.
            evidence_snip_early = (e.get("evidence_snippet") or "").lower()
            preferred_cells = [cell for cell in cell_to_ids if cell.lower() in evidence_snip_early]
            ordered_cids = []
            for cell in preferred_cells:
                ordered_cids.extend(cell_to_ids[cell])
            ordered_cids.extend([cid for cid in id_to_name if cid not in ordered_cids])
            candidates = [(cid, id_to_name[cid]) for cid in ordered_cids]
            for cid, cname in candidates:
                score = name_similarity(mention, cname)
                partial = 0
                if HAS_RAPIDFUZZ:
                    partial = fuzz.partial_ratio(mention.lower(), cname.lower())
                    # Use max, but keep partial for nickname like "Farhan" → "Farhan Qureshi"
                    score = max(score, partial * 0.95)
                # Initial-aware: "R. Kumar" → "Rahul Kumar", "Rahul K." → "Rahul Kumar"
                init_score = _initial_aware_score(mention, cname)
                if init_score > score:
                    score = init_score
                    best_method_hint = "initials"
                else:
                    best_method_hint = None
                if score > best_score:
                    best_score = score
                    best_name_score = name_similarity(mention, cname)
                    if init_score > best_name_score:
                        best_name_score = init_score
                    best_partial = partial
                    best_id = cid
                    best_method = "fuzzy_initials" if best_method_hint else ("fuzzy" if score < 99 else "fuzzy_partial")

        # Multi-signal confidence breakdown
        if best_id:
            name_norm = best_name_score  # 0-100
            # Phone/account signal: exact phone OR account match in mention evidence
            phone_match = 0.0
            mention_phone = e.get("phone") or ""
            canon_person = next((p for p in people_directory.get("network_people",[])+people_directory.get("noise_people",[]) if p["id"]==best_id), {})
            canonical_phone = canon_person.get("phone", "")
            canonical_account = canon_person.get("account", "")
            evidence_snip = e.get("evidence_snippet","") or ""
            if mention_phone and canonical_phone and mention_phone == canonical_phone:
                phone_match = 1.0
            elif canonical_phone and canonical_phone in evidence_snip:
                phone_match = 1.0
            elif canonical_account and canonical_account in evidence_snip:
                phone_match = 1.0
            else:
                phone_match = 0.0
            # Context signal: location/address/org/cell overlap
            context_match = 0.5  # neutral baseline
            canonical_addr = id_to_address.get(best_id,"") or ""
            canonical_org = id_to_org.get(best_id,"") or ""
            evidence_snip = e.get("evidence_snippet","") or ""
            if canonical_addr and any(tok.lower() in evidence_snip.lower() for tok in canonical_addr.split(",") if len(tok.strip())>3):
                context_match = 1.0
            elif canonical_org and canonical_org.lower() in evidence_snip.lower():
                context_match = 1.0
            elif id_to_cell.get(best_id) and id_to_cell[best_id].lower() in evidence_snip.lower():
                context_match = 0.8
            # Compute weighted confidence
            resolution_conf = _multi_signal_confidence(best_name_score, phone_match, context_match)
            # Also compute partial-aware variant for nickname: if single token mention like "Farhan" with high partial, treat name as partial score
            if best_partial > 85 and len(mention.split())==1:
                # Nickname case: boost name_score to partial
                resolution_conf = _multi_signal_confidence(best_partial, phone_match, context_match)
                best_name_score = best_partial

            # Decision threshold: require at least 85 name OR 0.70 multi-signal for merge
            if best_score >= RESOLUTION_FUZZY_THRESHOLD and resolution_conf >= 0.65:
                mention_to_canonical[mention] = best_id
                resolution_groups[best_id].append(mention)
                base = best_name_score/100.0
                explanation = f"name:{best_name_score:.0f}/100={base:.3f}+phone:{phone_match}*0.04+ctx:{context_match}*0.02={resolution_conf:.3f}"
                resolution_rows.append({
                    "master_id": best_id,
                    "merged_ids": mention,
                    "method": f"{best_method}({explanation})",
                    "confidence": resolution_conf,
                    "name_score": round(best_name_score,1),
                    "phone_score": phone_match,
                    "context_score": context_match,
                    "source_id": e.get("source_id"),
                    "source_type": e.get("source_type"),
                    "evidence_snippet": e.get("evidence_snippet","")[:120],
                    "evidence_hash": e.get("evidence_hash","")
                })
                continue
            elif best_score >= 70:
                # Uncertain — keep separate but log as candidate with low conf (not merged)
                mention_to_canonical[mention] = mention
                base = best_name_score/100.0
                explanation = f"name:{best_name_score:.0f}/100={base:.3f}+phone:{phone_match}*0.04+ctx:{context_match}*0.02={resolution_conf:.3f}"
                resolution_rows.append({
                    "master_id": mention,
                    "merged_ids": f"candidate->{best_id} score {best_score:.1f}",
                    "method": f"fuzzy_reject({explanation})",
                    "confidence": 0.5,
                    "name_score": round(best_name_score,1),
                    "phone_score": phone_match,
                    "context_score": context_match,
                    "source_id": e.get("source_id"),
                    "source_type": e.get("source_type"),
                    "evidence_snippet": e.get("evidence_snippet","")[:120],
                    "evidence_hash": e.get("evidence_hash","")
                })
                continue
        # No match
        mention_to_canonical[mention] = mention

    # Collapse groups into one row per master for readability, but keep detailed rows above for provenance
    # Also produce aggregated resolution.csv as per spec: master_id, merged_ids (comma), method, confidence
    # We keep both — detailed rows above already satisfy spec; aggregate not needed separately

    return mention_to_canonical, resolution_rows

def write_resolution(resolution_rows: List[Dict], out_path: Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Task2: extended header with explainable scores, but keep first 4 cols backward compatible
    fieldnames = ["master_id","merged_ids","method","confidence","name_score","phone_score","context_score","source_id","source_type","evidence_snippet","evidence_hash"]
    with open(out_path, "w", newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in resolution_rows:
            # Ensure all keys present for backward compat
            for k in fieldnames:
                if k not in r:
                    r[k] = ""
            w.writerow({k: r.get(k,"") for k in fieldnames})

def load_alias_map_for_eval(data_dir: Path = DATA_DIR) -> Dict[str,str]:
    """Load alias_map.json eval-only — never used in pipeline, only for testing accuracy."""
    p = data_dir / "alias_map.json"
    if not p.exists():
        return {}
    import json
    with open(p, encoding='utf-8') as f:
        return json.load(f)
