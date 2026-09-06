"""
Investigation Store — filesystem-based, no DB.

Layout: data/investigations/{id}/
  meta.json {id, name, description, created, status, files:[{original, stored, format, detected_type, columns, mapping, validated}]}
  files/  original uploads
  mapped/ normalized json (for debugging)
  output/ graph.json etc per investigation (graph_{id}.json)
"""
import json
import uuid
import shutil
from pathlib import Path
from datetime import datetime
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "investigations"
ROOT.mkdir(parents=True, exist_ok=True)

MAX_STORED_INVESTIGATIONS = 8

def _id() -> str:
    return uuid.uuid4().hex[:8]

def prune_old_investigations(max_keep: int = MAX_STORED_INVESTIGATIONS):
    """
    Keeps only the most recent `max_keep` investigations (5-10 range, default 8),
    automatically deleting older investigation folders from disk.
    """
    invs = []
    for p in ROOT.iterdir():
        if p.is_dir() and (p / "meta.json").exists():
            try:
                meta = json.loads((p / "meta.json").read_text())
                invs.append((meta.get("created", ""), p.name))
            except Exception:
                pass
    invs.sort(key=lambda x: x[0], reverse=True)
    if len(invs) > max_keep:
        for created, iid in invs[max_keep:]:
            delete_investigation(iid)

def create_investigation(name: str, description: str = "") -> Dict:
    iid = _id()
    meta = {
        "id": iid,
        "name": name,
        "description": description,
        "created": datetime.utcnow().isoformat()+"Z",
        "status": "created",
        "files": [],
        "mapping": {},
        "processing": {}
    }
    (ROOT / iid).mkdir(parents=True, exist_ok=True)
    (ROOT / iid / "files").mkdir(exist_ok=True)
    (ROOT / iid / "mapped").mkdir(exist_ok=True)
    (ROOT / iid / "output").mkdir(exist_ok=True)
    save_meta(iid, meta)
    prune_old_investigations(MAX_STORED_INVESTIGATIONS)
    return meta

def list_investigations() -> List[Dict]:
    prune_old_investigations(MAX_STORED_INVESTIGATIONS)
    out=[]
    for p in ROOT.iterdir():
        if p.is_dir() and (p / "meta.json").exists():
            try:
                out.append(json.loads((p / "meta.json").read_text()))
            except Exception:
                pass
    # Sort descending by created timestamp and return only up to MAX_STORED_INVESTIGATIONS
    out.sort(key=lambda x: x.get("created",""), reverse=True)
    return out[:MAX_STORED_INVESTIGATIONS]

def get_meta(iid: str) -> Dict:
    p = ROOT / iid / "meta.json"
    if not p.exists():
        raise FileNotFoundError(f"Investigation {iid} not found")
    return json.loads(p.read_text())


def delete_investigation(iid: str) -> bool:
    # Guard against path traversal: only delete direct children of ROOT
    if not iid or iid in (".", "..") or "/" in iid or "\\" in iid:
        return False
    target = ROOT / iid
    if target.exists() and target.is_dir():
        shutil.rmtree(target)
        return True
    return False

def save_meta(iid: str, meta: Dict):
    (ROOT / iid / "meta.json").write_text(json.dumps(meta, indent=2))

def add_file(iid: str, src_path: Path, detected: Dict, columns: List[str], original_name: str = None) -> Dict:
    meta = get_meta(iid)
    # Use the investigator-facing filename (temp copies carry a uuid prefix)
    fname = Path(original_name or src_path.name).name
    dest = ROOT / iid / "files" / fname
    shutil.copy(src_path, dest)
    entry = {
        "original": fname,
        "stored": str(dest.relative_to(ROOT)),
        "format": detected.get("format"),
        "detected_type": detected.get("detected_type"),
        "type_confidence": detected.get("type_confidence"),
        "columns": columns,
        "sample_rows": detected.get("sample_rows", [])[:2],
    }
    # Re-upload of the same filename replaces the previous entry (and its mapping)
    meta["files"] = [e for e in meta.get("files", []) if e.get("original") != fname]
    meta["files"].append(entry)
    if fname in meta.get("mapping", {}):
        del meta["mapping"][fname]
    meta["status"] = "files_uploaded"
    save_meta(iid, meta)
    return entry

def set_mapping(iid: str, file_name: str, mapping: Dict, validated: bool, missing: List[str]):
    meta = get_meta(iid)
    meta.setdefault("mapping", {})[file_name] = {"mapping": mapping, "validated": validated, "missing": missing}
    # Don't downgrade a finished/in-flight run when mappings are re-confirmed
    if meta.get("status") not in ("processing", "completed"):
        meta["status"] = "mapped" if validated else "mapping_required"
    save_meta(iid, meta)
    return meta

def set_processing(iid: str, status: str, detail: Dict = None):
    meta = get_meta(iid)
    meta["status"] = status
    if detail:
        meta["processing"] = detail
    save_meta(iid, meta)
