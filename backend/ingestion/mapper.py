"""
Schema Mapping — alias dictionary + fuzzy matching.

Pillar 3.C — 100+ telecom, banking & police column aliases + header cleaner.
Maps any reasonable column names (caller/source_phone etc.) to normalized
internal schema. Fast path: if headers exactly equal known synthetic,
auto-map without user.

Normalized schemas (internal keys used by pipeline):
- cdrs: call_id, caller_id, caller_name, caller_phone, callee_id, callee_name, callee_phone, timestamp, day, call_type, duration_sec, cell_tower_location
- transactions: txn_id, sender_id, sender_name, sender_account, receiver_id, receiver_name, receiver_account, amount_inr, timestamp, day, txn_type
- firs: fir_id, date, day, station, location, ipc_sections, narrative
"""
from typing import Dict, List, Tuple
import re
try:
    from rapidfuzz import fuzz
    HAS_RAPIDFUZZ = True
except Exception:
    HAS_RAPIDFUZZ = False
    import difflib

# ---------------------------------------------------------------------------
# Pillar 3.C — header cleaner
# ---------------------------------------------------------------------------

def clean_header(col: str) -> str:
    """Normalize a raw header: strip, collapse whitespace, unify separators.

    'Caller Phone ' -> 'caller_phone'; 'CALLER-PHONE' -> 'caller_phone'.
    """
    if col is None:
        return ""
    s = str(col).strip()
    s = re.sub(r"[\s\-./]+", "_", s)      # spaces/dashes/dots -> underscore
    s = re.sub(r"[^a-zA-Z0-9_]", "", s)   # drop stray symbols (keeps _)
    s = re.sub(r"_+", "_", s).strip("_")
    return s.lower()


def clean_headers(columns: List[str]) -> List[str]:
    """Apply clean_header to a whole header row."""
    return [clean_header(c) for c in columns]


# Normalized target -> list of aliases (lowercase, underscores).
# 100+ entries covering telecom CDR variants, banking remitter/beneficiary
# jargon and police FIR / free-text report vocabulary (plan lines 105-107).
ALIASES = {
    # ---- cdrs ----
    "call_id": ["call_id", "id", "cdr_id", "record_id", "callid", "cdr_no", "sr_no", "s_no"],
    "caller_id": ["caller_id", "caller", "source_id", "from_id", "callerid", "a_num",
                  "calling_no", "originating_no", "originator_id", "source_subscriber"],
    "caller_name": ["caller_name", "caller", "source_name", "from_name", "calling_name",
                    "originator_name", "a_party_name"],
    "caller_phone": ["caller_phone", "caller", "source_phone", "caller phone", "source phone",
                     "from_phone", "caller_number", "source_number", "phone1",
                     "msisdn", "a_num", "calling_no", "originating_no", "calling_number",
                     "a_number", "source_msisdn", "caller_msisdn", "calling_party",
                     "originating_number", "from_number", "caller_mobile", "source_mobile", "source"],
    "callee_id": ["callee_id", "callee", "receiver", "destination_id", "receiver_id", "to_id", "calleeid",
                  "b_num", "called_no", "terminating_no", "destination_subscriber"],
    "callee_name": ["callee_name", "callee", "receiver", "destination_name", "receiver_name", "to_name",
                    "called_name", "b_party_name", "destination_party_name"],
    "callee_phone": ["callee_phone", "callee", "receiver", "destination_phone", "receiver_phone", "to_phone",
                     "callee_number", "dest_phone", "phone2", "b_num", "called_no",
                     "terminating_no", "called_number", "b_number", "dest_msisdn",
                     "callee_msisdn", "called_party", "terminating_number", "to_number",
                     "callee_mobile", "destination_mobile", "destination"],
    "timestamp": ["timestamp", "event_time", "call_time", "date_time", "datetime", "time",
                  "event time", "call_datetime", "start_time", "call_date", "date", "cdr_date"],
    "day": ["day", "day_number", "daynum", "day_no", "day_count"],
    "call_type": ["call_type", "type", "category", "call_category", "call_class"],
    "duration_sec": ["duration_sec", "duration", "call_duration", "duration_seconds",
                     "length", "call_length", "talk_time", "duration_secs", "seconds"],
    "cell_tower_location": ["cell_tower_location", "tower", "location", "tower_location",
                            "cell_tower", "cell_id", "cell_site", "tower_id", "site_id",
                            "lac_cell", "cgi", "tower_address", "coverage_area"],
    # ---- transactions ----
    "txn_id": ["txn_id", "transaction_id", "id", "trans_id", "txn_no", "ref_no", "utr",
               "reference", "reference_no", "transaction_ref"],
    "sender_id": ["sender_id", "sender", "from_id", "source_id", "payer_id", "remitter",
                  "remitter_id", "debit_id", "originator", "payer", "from_account_id"],
    "sender_name": ["sender_name", "sender", "from_name", "payer_name", "remitter",
                    "remitter_name", "benefactor_name", "payer", "debit_name", "originator_name",
                    "sender_account_name"],
    "sender_account": ["sender_account", "sender_acc", "from_account", "payer_account",
                       "remitter", "remitter_account", "debit_acc", "debit_account",
                       "payer", "source_account", "from_acc", "debited_account"],
    "receiver_id": ["receiver_id", "receiver", "to_id", "destination_id", "payee_id",
                    "beneficiary", "beneficiary_id", "credit_id", "payee"],
    "receiver_name": ["receiver_name", "receiver", "to_name", "payee_name", "beneficiary",
                      "beneficiary_name", "payee", "credit_name", "receiver_account_name"],
    "receiver_account": ["receiver_account", "receiver_acc", "to_account", "payee_account",
                         "beneficiary", "beneficiary_account", "credit_acc", "credit_account",
                         "payee", "destination_account", "to_acc", "credited_account"],
    "amount_inr": ["amount_inr", "amount", "value", "sum", "inr", "price", "txn_val",
                   "txn_value", "txn_amount", "debit_inr", "credit_inr", "debit_amt",
                   "credit_amt", "transfer_amount", "particulars", "narration",
                   "transaction_value", "rs", "rupees", "amt"],
    "txn_type": ["txn_type", "type", "transaction_type", "payment_type", "mode",
                 "txn_mode", "channel", "transfer_type"],
    # ---- firs / free text ----
    "fir_id": ["fir_id", "case_id", "id", "report_id", "fir id", "fir_no", "fir_number",
               "case_no", "case_number", "crime_no", "complaint_no"],
    "date": ["date", "timestamp", "event_time", "incident_date", "date_of_incident",
             "fir_date", "report_date", "occurrence_date", "incident_time"],
    "station": ["station", "police_station", "station_name", "ps", "thana", "police_chowki",
                "outpost", "reporting_station"],
    "location": ["location", "place", "area", "ward", "site", "spot", "venue",
                 "place_of_occurrence", "incident_location", "crime_location"],
    "ipc_sections": ["ipc_sections", "ipc", "sections", "act", "law", "bns_sections",
                     " Sections".strip(), "charges", "legal_sections", "offence_sections",
                     "crime_type", "crime", "category"],
    "narrative": ["narrative", "description", "details", "text", "story", "incident",
                  "report", "facts", "brief_facts", "fact_summary", "allegation",
                  "allegations", "incident_summary", "incident_details", "brief_facts",
                  "complaint", "complaint_details", "crime_details", "case_details",
                  "fir_details", "summary", "remarks", "narration", "particulars",
                  "post_text", "content", "activity_notes"],
    "accused_name": ["accused_name", "accused", "accused_person", "suspect_name", "suspect"],
    "complainant_name": ["complainant_name", "complainant", "victim_name", "victim",
                         "informant", "informant_name", "petitioner"],
    # ---- social_posts ----
    "post_id": ["post_id", "post id", "id", "social_id", "tweet_id", "message_id"],
    "handle": ["handle", "username", "user", "author_handle", "author", "screen_name",
               "profile", "account_handle"],
    "person_id": ["person_id", "person id", "author_id", "user_id", "suspect_id", "pid", "id"],
    "location_tag": ["location_tag", "location tag", "location", "tagged_location", "place",
                     "geo_tag", "checkin"],
    "post_text": ["post_text", "post text", "post", "content", "message", "caption",
                  "tweet", "text", "status", "update", "narrative"],
    "hashtags": ["hashtags", "hashtag", "tags", "tag", "mention_tags"],
    # ---- criminal_history ----
    "record_id": ["record_id", "record id", "id", "history_id", "sheet_no", "row_id"],
    "alias": ["alias", "nickname", "aka", "alias_name", "other_name", "alias_names"],
    "dob": ["dob", "date_of_birth", "birth_date", "birthdate", "born_on", "age_dob"],
    "prior_offences": ["prior_offences", "prior offences", "offences", "offenses",
                       "criminal_record", "history", "previous_cases", "past_offences",
                       "convictions", "antecedents", "criminal_history", "prior_cases",
                       "cases", "category"],
    "gang_affiliation": ["gang_affiliation", "gang affiliation", "gang", "affiliation",
                         "group", "syndicate", "crew", "outfit", "organization"],
    "known_address": ["known_address", "known address", "address", "residence", "location",
                      "home_address", "last_known_address", "hideout"],
    # ---- intelligence_reports ----
    "source_reliability": ["source_reliability", "source reliability", "reliability",
                           "source_rating", "credibility", "trust_level", "grading"],
    "mentioned_entity_ids": ["mentioned_entity_ids", "mentioned entities", "entities",
                             "mentioned_ids", "entity_ids", "linked_ids"],
    # ---- surveillance_reports ----
    "team": ["team", "unit", "surveillance_team", "officer", "squad", "watch_team",
             "deployed_unit"],
    "confidence": ["confidence", "certainty", "reliability_score", "confidence_score"],
    "activity_notes": ["activity_notes", "activity notes", "notes", "observations",
                       "activity", "remarks", "details", "watch_log", "field_notes",
                       "surveillance_notes", "text", "description", "summary", "report", "narrative"],
    # ---- people directory ----
    "id": ["id", "person_id", "suspect_id", "pid", "code"],
    "name": ["name", "person_name", "full_name", "suspect_name", "individual_name"],
    "phone": ["phone", "phone_number", "mobile", "contact", "msisdn", "contact_number",
              "mobile_number"],
    "account": ["account", "account_number", "acc_no", "bank_account", "ac_no"],
    "cell": ["cell", "group", "faction", "unit_cell", "gang_cell"],
    "role": ["role", "designation", "position", "rank", "capacity", "function"],
    # ---- generic ----
    "report_id": ["report_id", "report id", "id", "intel_id", "surveillance_id"],
}

# Required fields per detected type (for validation)
REQUIRED = {
    "cdrs": ["caller_phone", "callee_phone"],
    "transactions": ["sender_id", "receiver_id", "amount_inr"],
    "firs": ["narrative"],
    "social_posts": ["post_text"],
    "criminal_history": ["name"],
    "intelligence_reports": ["narrative"],
    "surveillance_reports": ["activity_notes"],
    "people_directory": ["name"],
    "unknown": [],
}

def _norm(s: str) -> str:
    return re.sub(r'[^a-z0-9]', '', str(s).lower())

def suggest_mapping(columns: List[str], detected_type: str) -> Dict[str, str]:
    """
    Return {normalized_field: original_column or None if not found}
    Uses alias exact + fuzzy (>=85). Header-cleaner aware.
    """
    # Build target set for this type (must cover every REQUIRED field)
    targets_by_type = {
        "cdrs": ["call_id","caller_id","caller_name","caller_phone","callee_id","callee_name","callee_phone","timestamp","day","call_type","duration_sec","cell_tower_location"],
        "transactions": ["txn_id","sender_id","sender_name","sender_account","receiver_id","receiver_name","receiver_account","amount_inr","timestamp","day","txn_type"],
        "firs": ["fir_id","date","day","station","location","ipc_sections","narrative","accused_name","complainant_name"],
        "social_posts": ["post_id","handle","person_id","timestamp","day","location_tag","post_text","hashtags"],
        "criminal_history": ["record_id","person_id","name","alias","dob","prior_offences","gang_affiliation","known_address"],
        "intelligence_reports": ["report_id","date","day","source_reliability","narrative","mentioned_entity_ids"],
        "surveillance_reports": ["report_id","date","day","team","location","confidence","activity_notes"],
        "people_directory": ["id","name","phone","account","cell","role"],
    }
    targets = targets_by_type.get(detected_type)
    if targets is None:
        # unknown type: fall back to every known target
        targets = list(ALIASES.keys())

    # cleaned lookup: cleaned-header -> original column (preserves exact names)
    cleaned = {clean_header(c): c for c in columns}
    col_norm = {_norm(c): c for c in columns}
    col_low = {c.lower(): c for c in columns}
    mapping = {}
    used = set()
    # Priority order: phone/account/id fields first to avoid name fields stealing phone columns
    priority = {
        "caller_phone": 0, "callee_phone": 0, "sender_account": 0, "receiver_account": 0,
        "phone": 0, "account": 0,
        "caller_id": 1, "callee_id": 1, "sender_id": 1, "receiver_id": 1, "id": 1,
        "person_id": 1,
        "amount_inr": 1, "duration_sec": 2, "timestamp": 2, "date": 2, "day": 3,
    }
    # sort targets by priority (lower = earlier)
    sorted_targets = sorted(targets, key=lambda t: priority.get(t, 5))
    for tgt in sorted_targets:
        aliases = ALIASES.get(tgt, [tgt])
        found = None
        # 0) cleaned-header exact match (handles 'Caller Phone' vs 'caller_phone')
        for alias in aliases:
            if clean_header(alias) in cleaned and cleaned[clean_header(alias)] not in used:
                found = cleaned[clean_header(alias)]
                break
        # 1) exact alias match (normalized) — only if column not used
        if not found:
            for alias in aliases:
                an = _norm(alias)
                if an in col_norm and col_norm[an] not in used:
                    found = col_norm[an]
                    break
                if alias.lower() in col_low and col_low[alias.lower()] not in used:
                    found = col_low[alias.lower()]
                    break
        if found:
            mapping[tgt] = found
            used.add(found)
            continue
        # fuzzy — only consider unused columns, threshold 85
        best, best_score = None, 0
        for c in columns:
            if c in used:
                continue
            for alias in aliases:
                if HAS_RAPIDFUZZ:
                    score = fuzz.ratio(_norm(c), _norm(alias))
                else:
                    import difflib
                    score = difflib.SequenceMatcher(None, _norm(c), _norm(alias)).ratio()*100
                if score > best_score:
                    best_score, best = score, c
        if best_score >= 85:
            mapping[tgt] = best
            used.add(best)
        else:
            mapping[tgt] = None
    # Ensure all targets present (for those not in priority order, fill None if not mapped)
    for tgt in targets:
        if tgt not in mapping:
            mapping[tgt] = None
    return mapping

def validate_mapping(mapping: Dict[str, str], detected_type: str) -> Tuple[bool, List[str]]:
    missing = []
    if detected_type == "cdrs":
        has_caller = bool(mapping.get("caller_phone") or mapping.get("caller_id") or mapping.get("caller_name"))
        has_callee = bool(mapping.get("callee_phone") or mapping.get("callee_id") or mapping.get("callee_name"))
        if not has_caller:
            missing.append("caller_phone")
        if not has_callee:
            missing.append("callee_phone")
    elif detected_type == "transactions":
        has_sender = bool(mapping.get("sender_id") or mapping.get("sender_account") or mapping.get("sender_name"))
        has_receiver = bool(mapping.get("receiver_id") or mapping.get("receiver_account") or mapping.get("receiver_name"))
        has_amount = bool(mapping.get("amount_inr"))
        if not has_sender:
            missing.append("sender_id")
        if not has_receiver:
            missing.append("receiver_id")
        if not has_amount:
            missing.append("amount_inr")
    elif detected_type == "criminal_history":
        has_person = bool(mapping.get("name") or mapping.get("person_id") or mapping.get("record_id") or mapping.get("alias"))
        if not has_person:
            missing.append("name")
    elif detected_type == "surveillance_reports":
        has_notes = bool(mapping.get("activity_notes") or mapping.get("narrative") or mapping.get("location") or mapping.get("person_id"))
        if not has_notes:
            missing.append("activity_notes")
    elif detected_type == "firs":
        has_narrative = bool(mapping.get("narrative") or mapping.get("ipc_sections") or mapping.get("fir_id") or mapping.get("location"))
        if not has_narrative:
            missing.append("narrative")
    elif detected_type == "social_posts":
        has_text = bool(mapping.get("post_text") or mapping.get("narrative") or mapping.get("handle") or mapping.get("person_id"))
        if not has_text:
            missing.append("post_text")
    elif detected_type == "people_directory":
        has_id_or_name = bool(mapping.get("name") or mapping.get("id") or mapping.get("phone") or mapping.get("account"))
        if not has_id_or_name:
            missing.append("name")
    else:
        req = REQUIRED.get(detected_type, [])
        missing = [f for f in req if not mapping.get(f)]
    return (len(missing) == 0, missing)

def apply_mapping(rows: List[Dict], mapping: Dict[str, str]) -> List[Dict]:
    """Remap each row's keys from original to normalized."""
    out = []
    for row in rows:
        new = {}
        for norm_field, orig_col in mapping.items():
            if orig_col and orig_col in row:
                new[norm_field] = row[orig_col]
            elif orig_col is None:
                # keep as None/Missing to be quarantined later
                new[norm_field] = None
        # preserve extra fields that were not mapped but exist
        for k, v in row.items():
            if k not in mapping.values() and k not in new:
                new[k] = v
        out.append(new)
    return out
