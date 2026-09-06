"""
Normalization — mapped rows → pipeline's datasets dict.

Pillar 3.D — fault-tolerant cleansing:
- Currency/amount normalizer: ₹45,000.00, $12,500, 45000/-, 2.5L, 3.2Cr, 1.5k
- Date normalizer: ISO, DD/MM/YYYY, MM/DD/YYYY, epoch sec/ms, excel serial
- Row-level quarantine helper: broken rows get a reason, good rows continue
Keeps same keys as load_all: cdrs, transactions, firs etc., values normalized.
"""
from typing import Dict, List, Tuple
from datetime import datetime, timedelta
import re

SYNTHETIC_START = datetime(2026, 1, 1)

# ---------------------------------------------------------------------------
# Pillar 3.D — currency / amount normalizer (plan line 110)
# ---------------------------------------------------------------------------

_AMOUNT_RE = re.compile(r"^\s*([₹$€£]?\s*[\d,]+(?:\.\d+)?)\s*(/-|/-)?\s*([a-zA-Z]*)\s*$")

def parse_amount(value) -> int:
    """Clean currency strings to integer INR.

    Handles: '₹45,000.00' -> 45000, '$12,500' -> 12500, '45000/-' -> 45000,
    '2.5L'/'2.5 lakh' -> 250000, '3.2 Cr'/'3.2 crore' -> 32000000,
    '1.5k' -> 1500, '' / None -> 0.
    """
    if value is None:
        return 0
    s = str(value).strip()
    if s == "" or s.lower() in ("none", "nan", "null", "-", "na"):
        return 0
    low = s.lower().replace(",", "").replace("₹", "").replace("$", "").replace("€", "").replace("£", "").replace("rs.", "").replace("rs", "").replace("inr", "").strip()
    low = low.rstrip("/- ").strip()
    # suffix multipliers
    mult = 1
    m = re.match(r"^([\d.]+)\s*(crore|crores|cr|lakh|lakhs|l|lac|k|thousand|million|m)?\.?$", low)
    if m:
        num_s, suffix = m.group(1), (m.group(2) or "").lower()
        try:
            num = float(num_s)
        except ValueError:
            return 0
        if suffix in ("cr", "crore", "crores"):
            mult = 10_000_000
        elif suffix in ("l", "lac", "lakh", "lakhs"):
            mult = 100_000
        elif suffix in ("k", "thousand"):
            mult = 1_000
        elif suffix in ("m", "million"):
            mult = 1_000_000
        return int(num * mult)
    # plain number with decimals
    try:
        return int(float(low))
    except ValueError:
        # strip any remaining non-numeric chars
        digits = re.sub(r"[^0-9.]", "", low)
        try:
            return int(float(digits)) if digits else 0
        except ValueError:
            return 0


def clean_amount(value) -> int:
    return parse_amount(value)


# ---------------------------------------------------------------------------
# Pillar 3.D — date normalizer (plan line 111)
# ---------------------------------------------------------------------------

def parse_date(value):
    """Return ISO 'YYYY-MM-DD HH:MM:SS' string or '' if unparseable.

    Handles ISO, 'DD/MM/YYYY [HH:MM[:SS]]', 'MM/DD/YYYY', 'DD-MM-YYYY',
    epoch seconds (10-digit) / millis (13-digit), excel serial days.
    """
    if value is None:
        return ""
    s = str(value).strip()
    if not s or s.lower() in ("none", "nan", "null", "-", "na"):
        return ""
    # epoch millis / seconds (pure digits)
    if re.fullmatch(r"\d{13}", s):
        try:
            dt = datetime.utcfromtimestamp(int(s) / 1000.0)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    if re.fullmatch(r"\d{10}", s):
        try:
            dt = datetime.utcfromtimestamp(int(s))
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    # excel serial (e.g. 45658) — days since 1899-12-30, plausible range
    if re.fullmatch(r"\d{4,5}(\.0+)?", s):
        try:
            serial = float(s)
            if 20000 <= serial <= 80000:
                dt = datetime(1899, 12, 30) + timedelta(days=int(serial))
                return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    # ISO / common formats
    fmts = [
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S", "%Y/%m/%d",
        "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
        "%m/%d/%Y %H:%M:%S", "%m/%d/%Y",
        "%d-%m-%Y %H:%M:%S", "%d-%m-%Y", "%d-%m-%y",
        "%Y-%m-%dT%H:%M:%S", "%d %b %Y", "%d %B %Y",
        "%b %d, %Y", "%B %d, %Y",
    ]
    for fmt in fmts:
        try:
            dt = datetime.strptime(s.split(".")[0] if "T" not in s else s.split(".")[0], fmt)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
    # ambiguous DD/MM vs MM/DD: if first component > 12, force dayfirst
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})(?:\s+(\d{1,2}:\d{2}(?::\d{2})?))?", s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        t = m.group(4) or "00:00:00"
        if len(t) == 5:
            t += ":00"
        try:
            if a > 12:
                dt = datetime(y, b, a)
            elif b > 12:
                dt = datetime(y, b, a)
            else:
                # default dayfirst (Indian datasets): DD/MM/YYYY
                dt = datetime(y, b, a)
            return dt.strftime(f"%Y-%m-%d {t}")
        except Exception:
            pass
    try:
        from dateutil import parser as _dp
        dt = _dp.parse(s, dayfirst=True)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return s  # keep raw — downstream _to_day may still salvage


def clean_date(value) -> str:
    return parse_date(value)


def _to_day(timestamp_str):
    if not timestamp_str:
        return None
    s = str(timestamp_str).strip()
    if not s or s.lower() in ("none", "nan", "null"):
        return None
    # direct day int
    if re.fullmatch(r"\d{1,3}", s):
        try:
            d = int(s)
            if 1 <= d <= 365:
                return d
        except Exception:
            pass
    iso = parse_date(s)
    try:
        dt = datetime.fromisoformat(str(iso).replace(" ", "T").split(".")[0])
        delta = (dt - SYNTHETIC_START).days + 1
        if -365 <= delta <= 730:
            return delta if delta >= 1 else None
        return None
    except Exception:
        pass
    return None


def clean_phone(value) -> str:
    if value is None:
        return ""
    digits = re.sub(r"\D", "", str(value))
    # keep last 10 digits for Indian numbers with country code
    if len(digits) > 10 and digits.startswith("91"):
        digits = digits[-10:]
    return digits


def clean_duration(value) -> int:
    if value is None:
        return 0
    s = str(value).strip().lower()
    m = re.match(r"^([\d.]+)\s*(s|sec|secs|seconds|m|min|mins|minutes|h|hr|hrs|hours)?$", s)
    if m:
        try:
            num = float(m.group(1))
        except ValueError:
            return 0
        unit = (m.group(2) or "s").lower()
        if unit.startswith("h"):
            return int(num * 3600)
        if unit.startswith("m"):
            return int(num * 60)
        return int(num)
    try:
        return int(float(s))
    except ValueError:
        return 0


def normalize_cdrs(rows: List[Dict]) -> List[Dict]:
    out=[]
    for i, r in enumerate(rows):
        # fill missing ids
        call_id = r.get("call_id") or f"GEN-CDR{i+1:05d}"
        # timestamp fallback (date normalizer handles DD/MM/YYYY etc.)
        ts_raw = r.get("timestamp") or r.get("date") or ""
        ts = parse_date(ts_raw) if ts_raw else ""
        day = r.get("day")
        if not day or str(day).strip() in ("", "None"):
            day = _to_day(ts_raw or ts)
        
        caller_id = str(r.get("caller_id") or r.get("caller") or r.get("caller_name") or r.get("caller_phone") or f"UNK{i}")
        caller_name = str(r.get("caller_name") or r.get("caller") or r.get("caller_id") or "")
        caller_phone = clean_phone(r.get("caller_phone") or r.get("caller") or r.get("caller_id") or "")
        
        callee_id = str(r.get("callee_id") or r.get("callee") or r.get("receiver") or r.get("callee_name") or r.get("callee_phone") or f"UNK{i}")
        callee_name = str(r.get("callee_name") or r.get("callee") or r.get("receiver") or r.get("callee_id") or "")
        callee_phone = clean_phone(r.get("callee_phone") or r.get("callee") or r.get("receiver") or r.get("callee_id") or "")

        out.append({
            "call_id": str(call_id),
            "caller_id": caller_id,
            "caller_name": caller_name,
            "caller_phone": caller_phone,
            "callee_id": callee_id,
            "callee_name": callee_name,
            "callee_phone": callee_phone,
            "timestamp": str(ts or ts_raw),
            "day": int(day) if str(day).isdigit() else day,
            "call_type": str(r.get("call_type") or r.get("type") or "voice"),
            "duration_sec": clean_duration(r.get("duration_sec") or 0),
            "cell_tower_location": str(r.get("cell_tower_location") or r.get("location") or "Unknown"),
        })
    return out

def normalize_transactions(rows: List[Dict]) -> List[Dict]:
    out=[]
    for i, r in enumerate(rows):
        txn_id = r.get("txn_id") or f"GEN-TXN{i+1:05d}"
        ts_raw = r.get("timestamp") or r.get("date") or ""
        ts = parse_date(ts_raw) if ts_raw else ""
        day = r.get("day") or _to_day(ts_raw or ts)
        amt = parse_amount(r.get("amount_inr") if r.get("amount_inr") not in (None, "") else r.get("amount", 0))
        out.append({
            "txn_id": str(txn_id),
            "sender_id": str(r.get("sender_id") or r.get("sender_name") or f"UNK{i}"),
            "sender_name": str(r.get("sender_name") or r.get("sender_id") or ""),
            "sender_account": str(r.get("sender_account") or "").strip(),
            "receiver_id": str(r.get("receiver_id") or r.get("receiver_name") or f"UNK{i}"),
            "receiver_name": str(r.get("receiver_name") or r.get("receiver_id") or ""),
            "receiver_account": str(r.get("receiver_account") or "").strip(),
            "amount_inr": amt,
            "timestamp": str(ts or ts_raw),
            "day": int(day) if str(day).isdigit() else day,
            "txn_type": str(r.get("txn_type") or r.get("type") or "Bank Transfer"),
        })
    return out

def normalize_firs(rows: List[Dict]) -> List[Dict]:
    out=[]
    for i, r in enumerate(rows):
        fid = r.get("fir_id") or r.get("report_id") or f"GEN-FIR{i+1:04d}"
        date_raw = r.get("date") or r.get("timestamp") or ""
        date = parse_date(date_raw) if date_raw else ""
        out.append({
            "fir_id": str(fid),
            "date": str(date or date_raw),
            "day": r.get("day") or _to_day(date_raw or date),
            "station": str(r.get("station") or ""),
            "location": str(r.get("location") or ""),
            "ipc_sections": str(r.get("ipc_sections") or r.get("crime_type") or ""),
            "narrative": str(r.get("narrative") or r.get("description") or r.get("text") or r.get("facts") or r.get("allegation") or r.get("incident_summary") or ""),
            "accused_name": str(r.get("accused_name") or ""),
            "complainant_name": str(r.get("complainant_name") or ""),
        })
    return out


def normalize_social_posts(rows: List[Dict]) -> List[Dict]:
    out = []
    for i, r in enumerate(rows):
        ts_raw = r.get("timestamp") or r.get("date") or ""
        ts = parse_date(ts_raw) if ts_raw else ""
        out.append({
            "post_id": str(r.get("post_id") or f"GEN-POST{i+1:04d}"),
            "handle": str(r.get("handle") or ""),
            "person_id": str(r.get("person_id") or ""),
            "timestamp": str(ts or ts_raw),
            "day": r.get("day") or _to_day(ts_raw or ts),
            "location_tag": str(r.get("location_tag") or r.get("location") or ""),
            "post_text": str(r.get("post_text") or r.get("text") or r.get("content") or ""),
            "hashtags": str(r.get("hashtags") or ""),
        })
    return out


def normalize_criminal_history(rows: List[Dict]) -> List[Dict]:
    out = []
    for i, r in enumerate(rows):
        pid = str(r.get("person_id") or r.get("name") or r.get("alias") or f"GEN-P{i+1:03d}")
        name = str(r.get("name") or r.get("person_id") or r.get("alias") or "")
        out.append({
            "record_id": str(r.get("record_id") or r.get("history_id") or f"GEN-REC{i+1:04d}"),
            "person_id": pid,
            "name": name,
            "alias": str(r.get("alias") or ""),
            "dob": parse_date(r.get("dob")) if r.get("dob") else str(r.get("dob") or ""),
            "prior_offences": str(r.get("prior_offences") or r.get("prior_cases") or r.get("category") or ""),
            "gang_affiliation": str(r.get("gang_affiliation") or ""),
            "known_address": str(r.get("known_address") or r.get("location") or ""),
        })
    return out


def normalize_intelligence_reports(rows: List[Dict]) -> List[Dict]:
    out = []
    for i, r in enumerate(rows):
        date_raw = r.get("date") or r.get("timestamp") or ""
        date = parse_date(date_raw) if date_raw else ""
        out.append({
            "report_id": str(r.get("report_id") or f"GEN-INTEL{i+1:04d}"),
            "date": str(date or date_raw),
            "day": r.get("day") or _to_day(date_raw or date),
            "source_reliability": str(r.get("source_reliability") or ""),
            "narrative": str(r.get("narrative") or r.get("text") or ""),
            "mentioned_entity_ids": str(r.get("mentioned_entity_ids") or ""),
        })
    return out


def normalize_surveillance_reports(rows: List[Dict]) -> List[Dict]:
    out = []
    for i, r in enumerate(rows):
        date_raw = r.get("date") or r.get("timestamp") or ""
        date = parse_date(date_raw) if date_raw else ""
        conf = r.get("confidence")
        try:
            conf = float(str(conf).strip()) if conf not in (None, "") else ""
        except ValueError:
            pass
        out.append({
            "report_id": str(r.get("report_id") or r.get("surveillance_id") or f"GEN-SURV{i+1:04d}"),
            "date": str(date or date_raw),
            "day": r.get("day") or _to_day(date_raw or date),
            "team": str(r.get("team") or ""),
            "location": str(r.get("location") or ""),
            "confidence": conf if conf != "" else "",
            "activity_notes": str(r.get("activity_notes") or r.get("text") or r.get("description") or r.get("notes") or r.get("details") or ""),
        })
    return out


def normalize_generic(rows: List[Dict], dtype: str) -> List[Dict]:
    if dtype == "cdrs":
        return normalize_cdrs(rows)
    if dtype == "transactions":
        return normalize_transactions(rows)
    if dtype == "firs":
        return normalize_firs(rows)
    if dtype == "social_posts":
        return normalize_social_posts(rows)
    if dtype == "criminal_history":
        return normalize_criminal_history(rows)
    if dtype == "intelligence_reports":
        return normalize_intelligence_reports(rows)
    if dtype == "surveillance_reports":
        return normalize_surveillance_reports(rows)
    if dtype == "people_directory":
        return rows
    # fallback: return as-is (unknown types flow to FIR-like handling upstream)
    return rows


# ---------------------------------------------------------------------------
# Pillar 3.D — row-level quarantine (plan line 112)
# ---------------------------------------------------------------------------

def normalize_with_quarantine(rows: List[Dict], dtype: str,
                              source_file: str = "") -> Tuple[List[Dict], List[Dict]]:
    """Normalize + quarantine broken rows individually.

    A row is quarantined (with a specific reason) when core essential fields
    are empty after normalization; every other row flows into the graph.
    Returns (good_rows, quarantine_rows[{row_no, source_file, reason, confidence}]).
    """
    normed = normalize_generic(rows, dtype)
    good: List[Dict] = []
    quarantined: List[Dict] = []
    for idx, (raw, n) in enumerate(zip(rows, normed), start=2):
        missing = []
        if dtype == "cdrs":
            has_caller = bool(str(n.get("caller_phone") or "").strip() or str(n.get("caller_id") or "").strip() or str(n.get("caller_name") or "").strip())
            has_callee = bool(str(n.get("callee_phone") or "").strip() or str(n.get("callee_id") or "").strip() or str(n.get("callee_name") or "").strip())
            if not has_caller:
                missing.append("caller_phone / caller_id")
            if not has_callee:
                missing.append("callee_phone / callee_id")
        elif dtype == "transactions":
            has_sender = bool(str(n.get("sender_id") or "").strip() or str(n.get("sender_account") or "").strip() or str(n.get("sender_name") or "").strip())
            has_receiver = bool(str(n.get("receiver_id") or "").strip() or str(n.get("receiver_account") or "").strip() or str(n.get("receiver_name") or "").strip())
            if not has_sender:
                missing.append("sender_id / sender_account")
            if not has_receiver:
                missing.append("receiver_id / receiver_account")
        elif dtype == "criminal_history":
            has_person = bool(str(n.get("name") or "").strip() or str(n.get("person_id") or "").strip() or str(n.get("record_id") or "").strip() or str(n.get("alias") or "").strip())
            if not has_person:
                missing.append("name / person_id")
        elif dtype == "surveillance_reports":
            has_notes = bool(str(n.get("activity_notes") or "").strip() or str(n.get("location") or "").strip() or str(n.get("team") or "").strip())
            if not has_notes:
                missing.append("activity_notes")
        elif dtype == "firs":
            narrative = str(n.get("narrative") or "").strip()
            if len(narrative) < 2 and not str(n.get("ipc_sections") or "").strip():
                missing.append("narrative")
        elif dtype == "social_posts":
            text = str(n.get("post_text") or "").strip()
            if len(text) < 2 and not str(n.get("handle") or "").strip():
                missing.append("post_text")
        elif dtype == "people_directory":
            has_name = bool(str(n.get("name") or "").strip() or str(n.get("id") or "").strip() or str(n.get("phone") or "").strip())
            if not has_name:
                missing.append("name / id")

        if missing:
            quarantined.append({
                "row_no": idx,
                "source_file": source_file,
                "reason": f"Missing/invalid required field(s): {', '.join(missing)}",
                "confidence": 0.0,
            })
            continue
        good.append(n)
    return good, quarantined
