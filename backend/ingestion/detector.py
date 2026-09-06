"""
Format + Dataset Type + Schema Detection (Task-agnostic).

Pillar 3 — Universal Dataset Compatibility & Fault-Tolerant Ingestion:
- Multi-encoding sniffer: utf-8-sig -> utf-8 -> latin-1 -> cp1252 -> iso-8859-1
- Delimiter sniffing: comma, semicolon, tab, pipe
- Handlers: CSV, TSV/TXT/LOG (table vs free-text), multi-sheet XLSX/XLS,
  JSON, Word DOCX + legal PDFs (paragraph/FIR extraction), ZIP contents
  are expanded by the API layer before reaching this module.
- Header cleaner + heuristic dataset-type scoring (no hard-coded case logic).
"""
import csv
import json
import re
import zipfile
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from xml.etree import ElementTree as ET

SUPPORTED_FORMATS = {
    ".csv": "csv",
    ".xlsx": "xlsx",
    ".xls": "xlsx",
    ".json": "json",
    ".txt": "txt",
    ".log": "txt",
    ".tsv": "txt",
    ".pdf": "pdf",
    ".docx": "docx",
}

# Pillar 3.B — encoding trial order (plan line 101)
ENCODING_SEQUENCE = ["utf-8-sig", "utf-8", "latin-1", "cp1252", "iso-8859-1"]

# Pillar 3.B — delimiter candidates (plan line 102)
DELIMITERS = [",", ";", "\t", "|"]

# Heuristic keywords per normalized dataset type
TYPE_KEYWORDS = {
    "cdrs": ["caller", "callee", "phone", "call", "duration", "tower", "caller_phone", "callee_phone",
             "msisdn", "cell_id", "imei", "imsi", "cdr"],
    "transactions": ["sender", "receiver", "amount", "account", "txn", "transfer", "balance",
                     "remitter", "beneficiary", "payer", "payee", "debit", "credit", "utr"],
    "firs": ["fir", "ipc", "narrative", "station", "complainant", "accused", "facts",
             "allegation", "incident"],
    "social_posts": ["post", "handle", "hashtag", "social", "tweet", "caption", "message"],
    "criminal_history": ["criminal", "history", "offence", "offense", "gang", "alias", "dob",
                         "prior", "affiliation"],
    "intelligence_reports": ["intelligence", "source_reliability", "informant", "source"],
    "surveillance_reports": ["surveillance", "team", "activity_notes", "vehicle", "activity",
                             "observations"],
    "people_directory": ["person", "people", "directory", "role", "cell", "phone", "account"],
}

FIR_NO_RE = re.compile(r"\b(?:FIR\s*(?:No\.?|Number)?\s*[:#-]?\s*(\d+[\w\-/]*))\b", re.IGNORECASE)
DATE_RE = re.compile(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{1,2}-\d{1,2})[^0-9]*(\d{1,2}:\d{2}(:\d{2})?)?")
ACCUSED_RE = re.compile(r"\baccused\s*[:\-]?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})")


# ---------------------------------------------------------------------------
# Pillar 3.B — multi-encoding sniffer
# ---------------------------------------------------------------------------

def read_text_with_encoding(file_path: Path) -> Tuple[str, str]:
    """Try ENCODING_SEQUENCE in order; return (text, encoding_used)."""
    raw = Path(file_path).read_bytes()
    for enc in ENCODING_SEQUENCE:
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    # last resort: replace errors
    return raw.decode("utf-8", errors="replace"), "utf-8-replace"


def sniff_delimiter(sample: str) -> str:
    """Pick the best delimiter from DELIMITERS via csv.Sniffer, fallback to counts."""
    try:
        dialect = csv.Sniffer().sniff(sample[:8192], delimiters="".join(DELIMITERS))
        if dialect.delimiter in DELIMITERS:
            return dialect.delimiter
    except Exception:
        pass
    # fallback: most frequent delimiter on the first non-empty lines
    lines = [ln for ln in sample.splitlines() if ln.strip()][:5]
    if not lines:
        return ","
    scores = {d: sum(ln.count(d) for ln in lines) for d in DELIMITERS}
    best = max(scores, key=lambda d: scores[d])
    return best if scores[best] > 0 else ","


# ---------------------------------------------------------------------------
# Pillar 3.A — document text extractors (dependency-light, graceful fallback)
# ---------------------------------------------------------------------------

def extract_docx_text(file_path: Path) -> str:
    """Extract paragraphs from .docx (OOXML zip). Tries python-docx first."""
    try:
        import docx  # type: ignore
        doc = docx.Document(str(file_path))
        return "\n".join(p.text for p in doc.paragraphs if p.text and p.text.strip())
    except ImportError:
        pass
    except Exception:
        pass
    # Fallback: raw OOXML parse (no dependency)
    try:
        with zipfile.ZipFile(str(file_path)) as zf:
            xml_bytes = zf.read("word/document.xml")
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        root = ET.fromstring(xml_bytes)
        paras = []
        for p in root.findall(".//w:p", ns):
            texts = [t.text or "" for t in p.findall(".//w:t", ns)]
            line = "".join(texts).strip()
            if line:
                paras.append(line)
        if paras:
            return "\n".join(paras)
    except Exception:
        pass
    # Last resort: treat as plain text with encoding sniffer
    try:
        text, _ = read_text_with_encoding(file_path)
        return text
    except Exception:
        return ""


def extract_pdf_text(file_path: Path) -> str:
    """Extract text from legal PDFs. Tries pypdf/PyPDF2/pdfminer, else empty."""
    for mod_name, reader_fn in [
        ("pypdf", lambda m, p: "\n".join((pg.extract_text() or "") for pg in m.PdfReader(str(p)).pages)),
        ("PyPDF2", lambda m, p: "\n".join((pg.extract_text() or "") for pg in m.PdfReader(str(p)).pages)),
        ("pdfminer.high_level", lambda m, p: m.extract_text(str(p))),
    ]:
        try:
            mod = __import__(mod_name, fromlist=["*"])
            text = reader_fn(mod, file_path) or ""
            if text.strip():
                return text
        except ImportError:
            continue
        except Exception:
            continue
    return ""


def extract_document_rows(text: str, source_name: str = "") -> List[Dict]:
    """Chunk free-form document text into narrative rows (FIR/Intel style).

    Each non-trivial paragraph becomes one row with extracted FIR number,
    accused-name hint and date hint so downstream mapping has something
    to normalize.
    """
    paras = [p.strip() for p in re.split(r"\n\s*\n|\r\n\s*\r\n", text or "") if p.strip()]
    if not paras:
        # fall back to line-based chunking
        paras = [ln.strip() for ln in (text or "").splitlines() if len(ln.strip()) > 40]
    if not paras and (text or "").strip():
        paras = [(text or "").strip()[:2000]]
    rows: List[Dict] = []
    for i, para in enumerate(paras, start=1):
        fir_m = FIR_NO_RE.search(para)
        dt_m = DATE_RE.search(para)
        acc_m = ACCUSED_RE.search(para)
        rows.append({
            "fir_id": fir_m.group(1) if fir_m else f"DOC-{i:04d}",
            "date": dt_m.group(0).strip() if dt_m else "",
            "station": "",
            "location": "",
            "ipc_sections": "",
            "narrative": para[:4000],
            "accused_name": acc_m.group(1) if acc_m else "",
            "_source_doc": source_name,
        })
    return rows


# ---------------------------------------------------------------------------
# Flexible readers
# ---------------------------------------------------------------------------

def read_csv_flexible(file_path: Path, max_rows: Optional[int] = None) -> Tuple[List[str], List[Dict], Dict]:
    """Read CSV with multi-encoding + delimiter sniffing. Returns (cols, rows, meta)."""
    text, encoding = read_text_with_encoding(file_path)
    if not text.strip():
        return [], [], {"encoding": encoding, "delimiter": ",", "row_count_estimate": 0}
    delimiter = sniff_delimiter(text)
    lines = text.splitlines()
    try:
        reader = csv.DictReader(lines, delimiter=delimiter)
        cols = [c.strip() if c else "" for c in (reader.fieldnames or [])]
        cols = [c for c in cols if c]
        rows: List[Dict] = []
        for i, row in enumerate(reader):
            if max_rows is not None and i >= max_rows:
                break
            # normalize None keys (ragged rows)
            clean = {(k.strip() if isinstance(k, str) else k): (v if v is not None else "") for k, v in row.items() if k}
            if all(str(v).strip() == "" for v in clean.values()):
                continue
            rows.append(clean)
    except Exception:
        return [], [], {"encoding": encoding, "delimiter": delimiter, "row_count_estimate": 0}
    meta = {"encoding": encoding, "delimiter": delimiter, "row_count_estimate": len(rows)}
    return cols, rows, meta


def read_txt_file(file_path: Path, max_rows: Optional[int] = None) -> Tuple[List[str], List[Dict], Dict]:
    """Handle .txt/.log/.tsv: delimiter table vs free-form narrative report."""
    text, encoding = read_text_with_encoding(file_path)
    stripped = text.strip()
    if not stripped:
        return [], [], {"encoding": encoding, "handler": "empty", "row_count_estimate": 0}
    # Heuristic: if first 2 non-empty lines share a delimiter with 2+ fields -> table
    sample_lines = [ln for ln in stripped.splitlines() if ln.strip()][:5]
    delimiter = sniff_delimiter(text)
    first_counts = [ln.count(delimiter) for ln in sample_lines[:2]] if sample_lines else [0]
    is_table = (
        delimiter != "," or sum(1 for ln in sample_lines if delimiter in ln) >= 2
    ) and len(first_counts) >= 1 and first_counts[0] >= 1 and all(c >= 1 for c in first_counts[:2])
    # TSV files are always tables
    if file_path.suffix.lower() == ".tsv":
        is_table = True
        delimiter = "\t"
    if is_table:
        try:
            reader = csv.DictReader(sample_lines + stripped.splitlines()[len(sample_lines):], delimiter=delimiter)
            # re-read from full text to keep header logic simple
            reader = csv.DictReader(stripped.splitlines(), delimiter=delimiter)
            cols = [c.strip() if c else "" for c in (reader.fieldnames or [])]
            cols = [c for c in cols if c]
            # sanity: header must look like words, not a sentence
            if cols and max(len(c.split()) for c in cols) <= 5:
                rows = []
                for i, row in enumerate(reader):
                    if max_rows is not None and i >= max_rows:
                        break
                    clean = {(k.strip() if isinstance(k, str) else k): (v if v is not None else "") for k, v in row.items() if k}
                    if all(str(v).strip() == "" for v in clean.values()):
                        continue
                    rows.append(clean)
                return cols, rows, {"encoding": encoding, "delimiter": delimiter,
                                    "handler": "delimited-table", "row_count_estimate": len(rows)}
        except Exception:
            pass
    # Free-form text report -> narrative rows ingested as FIR/Intel style
    rows = extract_document_rows(text, source_name=file_path.name)
    if max_rows is not None:
        rows = rows[:max_rows]
    cols = ["fir_id", "date", "station", "location", "ipc_sections", "narrative"] if rows else []
    return cols, rows, {"encoding": encoding, "handler": "free-text-narrative",
                        "row_count_estimate": len(rows)}


def read_excel_all_sheets(file_path: Path, max_rows_per_sheet: Optional[int] = None) -> Tuple[List[str], List[Dict], Dict]:
    """Read multi-sheet workbooks; each sheet is an independent dataset stream."""
    try:
        import pandas as pd
    except ImportError:
        return [], [], {"handler": "xlsx-no-pandas", "row_count_estimate": 0, "sheets": []}
    try:
        xls = pd.ExcelFile(str(file_path))
    except Exception:
        return [], [], {"handler": "xlsx-unreadable", "row_count_estimate": 0, "sheets": []}
    all_rows: List[Dict] = []
    sheets_info: List[Dict] = []
    union_cols: List[str] = []
    for sheet in xls.sheet_names:
        try:
            df = pd.read_excel(xls, sheet_name=sheet, dtype=str)
        except Exception:
            continue
        df = df.dropna(how="all")
        if df.empty:
            sheets_info.append({"sheet": sheet, "columns": [], "rows": 0})
            continue
        df.columns = [str(c).strip() for c in df.columns]
        df = df.fillna("")
        records = df.to_dict(orient="records")
        if max_rows_per_sheet is not None:
            records = records[:max_rows_per_sheet]
        for r in records:
            r["_sheet"] = sheet
        all_rows.extend(records)
        for c in df.columns:
            if c not in union_cols:
                union_cols.append(c)
        sheets_info.append({"sheet": sheet, "columns": list(df.columns), "rows": len(records)})
    meta = {"handler": "multi-sheet", "sheets": sheets_info, "row_count_estimate": len(all_rows)}
    if max_rows_per_sheet is not None and len(all_rows) > max_rows_per_sheet * max(len(sheets_info), 1):
        pass
    return union_cols, all_rows, meta


def read_document_file(file_path: Path, max_rows: Optional[int] = None) -> Tuple[List[str], List[Dict], Dict]:
    """Read .pdf / .docx into narrative rows."""
    ext = file_path.suffix.lower()
    if ext == ".docx":
        text = extract_docx_text(file_path)
        handler = "docx-paragraphs"
    else:
        text = extract_pdf_text(file_path)
        handler = "pdf-text"
    if not text.strip():
        return [], [], {"handler": handler, "row_count_estimate": 0,
                        "warning": f"No extractable text in {file_path.name} (scanned PDF needs OCR)"}
    rows = extract_document_rows(text, source_name=file_path.name)
    if max_rows is not None:
        rows = rows[:max_rows]
    cols = ["fir_id", "date", "station", "location", "ipc_sections", "narrative"]
    return cols, rows, {"encoding": "utf-8", "handler": handler, "row_count_estimate": len(rows)}


def detect_format(file_path: Path) -> str:
    ext = Path(file_path).suffix.lower()
    if ext in SUPPORTED_FORMATS:
        return SUPPORTED_FORMATS[ext]
    # sniff content
    try:
        text, _ = read_text_with_encoding(file_path)
        sample = text[:2048]
        if sample.strip().startswith(('{', '[')):
            return "json"
        sniffer = csv.Sniffer()
        sniffer.sniff(sample, delimiters=",;\t|")
        return "csv"
    except Exception:
        pass
    return "unknown"


def detect_columns(file_path: Path, fmt: str, max_rows: int = 3) -> Tuple[List[str], List[Dict]]:
    """Return (columns, sample_rows[<=max_rows]) without loading full file."""
    cols, sample = [], []
    try:
        if fmt == "csv":
            cols, rows, _ = read_csv_flexible(file_path, max_rows=max_rows)
            sample = rows[:max_rows]
        elif fmt == "txt":
            cols, rows, _ = read_txt_file(file_path, max_rows=max_rows)
            sample = rows[:max_rows]
        elif fmt == "xlsx":
            cols, rows, _ = read_excel_all_sheets(file_path, max_rows_per_sheet=max_rows)
            sample = rows[:max_rows]
        elif fmt in ("pdf", "docx"):
            cols, rows, _ = read_document_file(file_path, max_rows=max_rows)
            sample = rows[:max_rows]
        elif fmt == "json":
            with open(file_path, encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, dict) and "data" in data:
                    data = data["data"]
                if isinstance(data, list) and data:
                    cols = list(data[0].keys())
                    sample = data[:max_rows]
                elif isinstance(data, dict):
                    cols = list(data.keys())
                    sample = [data]
    except Exception:
        cols = []
    # normalize whitespace
    cols = [c.strip() for c in cols if c and str(c).strip()]
    return cols, sample


def read_full_rows(file_path: Path, fmt: str) -> Tuple[List[str], List[Dict], Dict]:
    """Read the ENTIRE file (used by the /process pipeline). Returns (cols, rows, meta)."""
    if fmt == "csv":
        return read_csv_flexible(file_path)
    if fmt == "txt":
        return read_txt_file(file_path)
    if fmt == "xlsx":
        return read_excel_all_sheets(file_path)
    if fmt in ("pdf", "docx"):
        return read_document_file(file_path)
    if fmt == "json":
        try:
            with open(file_path, encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, dict) and "data" in data:
                    data = data["data"]
                rows = data if isinstance(data, list) else [data]
                cols = list(rows[0].keys()) if rows and isinstance(rows[0], dict) else []
                return cols, rows, {"handler": "json", "row_count_estimate": len(rows)}
        except Exception as e:
            return [], [], {"handler": "json-error", "error": str(e), "row_count_estimate": 0}
    return [], [], {"handler": "unknown", "row_count_estimate": 0}


def detect_dataset_type(columns: List[str]) -> Tuple[str, float]:
    """Score each type by keyword overlap (0-1). Return best type and confidence."""
    col_low = " ".join(c.lower() for c in columns)
    scores = {}
    for dtype, keywords in TYPE_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in col_low)
        scores[dtype] = hits / len(keywords) if keywords else 0
        # bonus for exact canonical columns
        if dtype == "cdrs" and any(x in col_low for x in ["caller_phone", "callee_phone"]):
            scores[dtype] += 0.3
        if dtype == "transactions" and "amount" in col_low:
            scores[dtype] += 0.2
        if dtype == "firs" and "narrative" in col_low:
            scores[dtype] += 0.3
        if dtype == "people_directory" and all(x in col_low for x in ["phone", "account"]):
            scores[dtype] += 0.3
    best = max(scores, key=lambda k: scores[k])
    conf = min(1.0, scores[best])
    # if very low, mark as generic "unknown" but return best guess with low conf
    if conf < 0.15:
        return "unknown", conf
    return best, round(conf, 3)


def detect_schema(file_path: Path) -> Dict:
    fmt = detect_format(file_path)
    cols, sample = detect_columns(file_path, fmt)
    dtype, conf = detect_dataset_type(cols)
    # collect extended meta (encoding / delimiter / sheets) on a best-effort basis
    meta: Dict = {}
    try:
        if fmt == "csv":
            _, _, meta = read_csv_flexible(file_path, max_rows=1)
        elif fmt == "txt":
            _, _, meta = read_txt_file(file_path, max_rows=1)
        elif fmt == "xlsx":
            _, _, meta = read_excel_all_sheets(file_path, max_rows_per_sheet=1)
        elif fmt in ("pdf", "docx"):
            _, _, meta = read_document_file(file_path, max_rows=1)
    except Exception:
        meta = {}
    # free-text narratives default to FIR-like handling downstream
    return {
        "file": Path(file_path).name,
        "format": fmt,
        "columns": cols,
        "sample_rows": sample[:2],
        "detected_type": dtype,
        "type_confidence": conf,
        "row_count_estimate": meta.get("row_count_estimate"),
        "encoding": meta.get("encoding"),
        "delimiter": meta.get("delimiter"),
        "handler": meta.get("handler"),
        "sheets": meta.get("sheets", []),
    }
