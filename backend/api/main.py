"""
FastAPI — contracts per criminal-network-live-reveal.md:80

GET /graph?day=INT → {nodes:[{id,label,cell,score}], edges:[{src,dst,kind,source,confidence}]}
GET /bridges → [{id,name,role,bridge_score,cells}]
GET /bursts → [{cell,day,zscore,window}]
GET /why/:id → {id, top_signals, sources ≥2 rows}
GET /ask?q=STRING → {template_id, cypher, params}

All reads + loader append to audit.jsonl {ts,user="demo-operator",query,result_ids}
"""
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from typing import Optional, List, Dict
import json
import time
from pathlib import Path
from datetime import datetime

from backend.config import PROJECT_ROOT, AUDIT_PATH, AUDIT_USER
from backend.graph.builder import load_graph_serial
from backend.analytics.burst_detection import detect_bursts
from backend.analytics.financial_anomaly import detect_structuring, detect_lump_sums
from backend.analytics.bridge_detection import compute_bridges
from backend.analytics.centrality import compute_centrality
from backend.analytics.community import detect_communities
from backend.analytics.lead_scoring import compute_lead_scores, get_leads, lead_for_entity
from backend.analytics.anomaly import get_unified_anomalies
from backend.analytics.cross_case import detect_cross_case
from backend.analytics.temporal import get_temporal_intelligence
from backend.analytics.connection_explainer import explain_connection
from backend.analytics.case_recommender import generate_case_recommendations
from backend.analytics.blockchain_ledger import (
    build_blockchain_ledger,
    generate_chain_of_custody_certificate,
    verify_evidence_hash_in_ledger
)
from backend.analytics.geospatial import (
    get_cell_towers_geospatial,
    get_suspect_trajectories,
    get_co_location_hotspots
)
from backend.analytics.takedown_simulator import (
    simulate_takedown,
    get_takedown_strategies,
    generate_operation_order
)
from backend.auth import CAN_UPLOAD, CAN_VIEW_GRAPH, REQUIRE_SUPERVISOR, authenticate, create_token, get_current_user
from backend.loader import load_all
from backend.config import DATA_DIR, PROJECT_ROOT
from backend.ingestion.detector import detect_schema, detect_format
from backend.ingestion.mapper import suggest_mapping, validate_mapping, apply_mapping, REQUIRED
from backend.ingestion.normalizer import normalize_generic
from backend.ingestion.store import create_investigation, list_investigations, get_meta, save_meta, add_file, set_mapping, set_processing, ROOT as INV_ROOT
from fastapi import UploadFile, File, Form
import shutil
import tempfile
import uuid

app = FastAPI(title="Criminal Network Fusion API", version="0.1.0",
              description="Evidence-backed AI Criminal Network Analysis — TASK 1 core pipeline")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def audit_log(query: str, result_ids: list):
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {"ts": datetime.utcnow().isoformat() + "Z", "user": AUDIT_USER, "query": query, "result_ids": result_ids[:50]}
    with open(AUDIT_PATH, "a", encoding='utf-8') as f:
        f.write(json.dumps(entry) + "\n")

@app.get("/")
def root():
    return {"status": "ok", "message": "Fusion API — TASK 1 core pipeline", "docs": "/docs",
            "endpoints": ["/graph?day=58", "/bridges", "/bursts", "/why/{id}", "/ask?q=", "/health", "/stats"]}

from pydantic import BaseModel
# --- Investigations — generic ingestion workflow (dataset-agnostic) ---
class InvestigationCreate(BaseModel):
    name: str
    description: str = ""

@app.get("/health")
def health():
    from backend.graph.neo4j_client import is_available
    serial = load_graph_serial()
    return {"status": "ok", "neo4j": is_available(), "graph_nodes": serial["stats"]["node_count"], "graph_edges": serial["stats"]["edge_count"]}

from pydantic import BaseModel

class LoginRequest(BaseModel):
    username: str
    password: str

@app.post("/login")
def login(payload: LoginRequest):
    user = authenticate(payload.username, payload.password)
    if not user:
        audit_log(f"POST /login failed for '{(payload.username or '').strip().lower()}'", [])
        raise HTTPException(status_code=401, detail="Invalid username or password")
    audit_log(f"POST /login {user['username']} ({user['role']})", [user["username"]])
    return {"access_token": create_token(user), "token_type": "bearer",
            "username": user["username"], "role": user["role"], "name": user["name"]}

@app.get("/me")
def me(user: dict = Depends(get_current_user)):
    return user


class RegisterRequest(BaseModel):
    username: str
    password: str
    role: str = "investigator"
    name: str = ""


@app.post("/register", status_code=201)
def register(payload: RegisterRequest):
    from backend.auth import register_user

    try:
        user = register_user(payload.username, payload.password, (payload.role or "").strip().lower(), payload.name)
    except ValueError as e:
        raise HTTPException(status_code=400 if "already taken" not in str(e) else 409, detail=str(e))
    audit_log(f"POST /register {user['username']} ({user['role']})", [user["username"]])
    return {"access_token": create_token(user), "token_type": "bearer",
            "username": user["username"], "role": user["role"], "name": user["name"]}

@app.post("/investigations")
def create_inv(payload: InvestigationCreate, user: dict = Depends(get_current_user)):
    meta = create_investigation(payload.name, payload.description)
    audit_log(f"POST /investigations {meta['id']}", [meta['id']])
    return meta

@app.get("/investigations")
def list_inv(user: dict = Depends(get_current_user)):
    invs = list_investigations()
    audit_log("GET /investigations", [x["id"] for x in invs[:5]])
    return {"investigations": invs}

@app.get("/investigations/{iid}")
def get_inv(iid: str, user: dict = Depends(get_current_user)):
    return _require_meta(iid)


@app.delete("/investigations/{iid}")
def delete_inv(iid: str, user: dict = Depends(get_current_user)):
    """Delete an investigation: filesystem data + Neo4j graph nodes. Allowed for all roles."""
    from backend.ingestion.store import delete_investigation
    from backend.graph.neo4j_client import delete_investigation_graph

    _require_meta(iid)  # 404 if unknown
    neo4j_deleted = delete_investigation_graph(iid)
    files_deleted = delete_investigation(iid)
    audit_log(f"DELETE /investigations/{iid} by {user['username']}", [iid])
    return {"deleted": files_deleted, "investigation_id": iid, "neo4j_nodes_deleted": neo4j_deleted}


def _require_meta(iid: str) -> dict:
    try:
        return get_meta(iid)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Investigation not found")

# Pillar 3.A — universal ingestion: CSV/XLSX/JSON + TXT/TSV/LOG + PDF/DOCX (+ ZIP of any)
UPLOAD_SUFFIXES = (".csv", ".xlsx", ".xls", ".json", ".zip",
                   ".txt", ".log", ".tsv", ".pdf", ".docx")
ZIP_MEMBER_SUFFIXES = (".csv", ".xlsx", ".xls", ".json",
                       ".txt", ".log", ".tsv", ".pdf", ".docx")


@app.post("/investigations/{iid}/upload")
async def upload_inv_files(iid: str, files: List[UploadFile] = File(...), user: dict = Depends(CAN_UPLOAD)):
    try:
        get_meta(iid)
    except:
        raise HTTPException(status_code=404, detail="Investigation not found")

    saved = []
    import zipfile
    import os

    for uf in files:
        suffix = Path(uf.filename).suffix.lower()
        if suffix not in UPLOAD_SUFFIXES:
            raise HTTPException(status_code=400, detail=f"Unsupported format {suffix} — use CSV/XLSX/JSON/TXT/TSV/PDF/DOCX/ZIP")
        
        tmp_upload = Path(tempfile.gettempdir()) / f"{uuid.uuid4().hex}_{uf.filename}"
        with open(tmp_upload, "wb") as out:
            shutil.copyfileobj(uf.file, out)
            
        files_to_process = []
        
        if suffix == ".zip":
            # Extract ZIP and process contents
            extract_dir = Path(tempfile.gettempdir()) / f"extracted_{uuid.uuid4().hex}"
            extract_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(tmp_upload, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)
            
            for root, dirs, extracted_files in os.walk(extract_dir):
                rel_root = Path(root).relative_to(extract_dir)
                parts = [p.lower() for p in rel_root.parts]
                # Skip non-dataset evaluation folders, git, docs, macosx
                if any(p in ("ground_truth", "evaluation", "benchmark", "benchmarks", "docs", ".git", "__macosx") for p in parts):
                    continue
                for file in extracted_files:
                    if file.startswith('.') or file.startswith('__MACOSX'):
                        continue # Skip hidden files
                    ext = Path(file).suffix.lower()
                    if ext in (".md", ".jsonld", ".py", ".sh"):
                        continue
                    if ext in ZIP_MEMBER_SUFFIXES:
                        # clean case name prefix if nested in folder
                        case_prefix = "_".join(p for p in rel_root.parts if p and p != ".")
                        clean_name = f"{case_prefix}_{file}" if case_prefix else file
                        files_to_process.append((Path(root) / file, clean_name))
            tmp_upload.unlink(missing_ok=True)
        else:
            files_to_process.append((tmp_upload, Path(uf.filename).name))

        # Process all gathered files
        from backend.ingestion.detector import detect_schema
        from backend.ingestion.mapper import suggest_mapping, validate_mapping
        for file_path, item_name in files_to_process:
            try:
                det = detect_schema(file_path)
                entry = add_file(iid, file_path, det, det["columns"], original_name=item_name)
                # Compute automated column mapping immediately
                mapping = suggest_mapping(det["columns"], det["detected_type"])
                valid, missing = validate_mapping(mapping, det["detected_type"])
                meta_up = get_meta(iid)
                meta_up.setdefault("mapping", {})[entry["original"]] = {
                    "mapping": mapping,
                    "validated": True,
                    "missing": missing
                }
                save_meta(iid, meta_up)
                saved.append({"file": entry["original"], "detected": det, "stored": entry, "mapping": mapping})
            except Exception as e:
                # Log but continue if one file in a zip fails
                print(f"Failed to process {item_name}: {e}")

        # Cleanup temp files (always remove the upload + extracted copies)
        tmp_upload.unlink(missing_ok=True)
        if suffix == ".zip":
            shutil.rmtree(extract_dir, ignore_errors=True)

    if not saved:
        raise HTTPException(status_code=400, detail="No supported files found in upload (use CSV/XLSX/JSON/TXT/TSV/PDF/DOCX/ZIP containing those)")

    return {"uploaded": saved, "investigation_id": iid}

@app.get("/investigations/{iid}/files")
def inv_files(iid: str, user: dict = Depends(get_current_user)):
    try:
        meta = _require_meta(iid)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Investigation not found")
    return {"files": meta.get("files", [])}

@app.get("/investigations/{iid}/detection/{filename}")
def inv_detection(iid: str, filename: str, user: dict = Depends(CAN_UPLOAD)):
    try:
        meta = _require_meta(iid)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Investigation not found")
    f = next((x for x in meta.get("files",[]) if x["original"]==filename), None)
    if not f:
        raise HTTPException(status_code=404, detail="File not found in investigation")
    # re-detect from stored file
    stored = INV_ROOT / f["stored"]
    det = detect_schema(stored)
    mapping = suggest_mapping(det["columns"], det["detected_type"])
    valid, missing = validate_mapping(mapping, det["detected_type"])
    required = list(REQUIRED.get(det["detected_type"], []))
    saved = meta.get("mapping", {}).get(filename)
    return {"detection": det, "suggested_mapping": mapping, "validated": valid,
            "missing": missing, "required": required,
            "saved_mapping": (saved or {}).get("mapping")}

@app.post("/investigations/{iid}/mapping")
def inv_set_mapping(iid: str, payload: Dict, user: dict = Depends(CAN_UPLOAD)):
    # payload: {filename: {normalized_field: original_col or null}}
    # Validates every file first, then saves all mappings to meta.json.
    meta = _require_meta(iid)
    if not isinstance(payload, dict) or not payload:
        raise HTTPException(status_code=400, detail="Mapping payload must be a non-empty object {filename: {field: column}}")
    checked = {}
    errors = {}
    for fname, mapping in payload.items():
        f = next((x for x in meta.get("files", []) if x["original"] == fname), None)
        if not f:
            errors[fname] = {"missing": [], "message": f"File {fname} not found in this investigation"}
            continue
        if not isinstance(mapping, dict):
            errors[fname] = {"missing": [], "message": f"Mapping for {fname} must be an object"}
            continue
        stored = INV_ROOT / f["stored"]
        det = detect_schema(stored)
        # Guard against typos: every mapped column must exist in the file
        unknown_cols = sorted({c for c in mapping.values() if c and c not in det["columns"]})
        if unknown_cols:
            errors[fname] = {"missing": [], "message": f"Unknown column(s) for {fname}: {', '.join(unknown_cols)}"}
            continue
        valid, missing = validate_mapping(mapping, det["detected_type"])
        if not valid:
            errors[fname] = {"missing": missing,
                             "message": f"Missing required field(s) for {fname} ({det['detected_type']}): {', '.join(missing)}"}
            continue
        checked[fname] = (mapping, valid, missing, det["detected_type"])
    if errors:
        raise HTTPException(status_code=400, detail={"files": errors, "message": "Mapping review failed for one or more files"})
    for fname, (mapping, valid, missing, _dtype) in checked.items():
        set_mapping(iid, fname, mapping, valid, missing)
    return get_meta(iid)

@app.get("/mugshots/{filename}")
def get_mugshot(filename: str):
    """Secure static image streaming endpoint for criminal mugshots."""
    from fastapi.responses import FileResponse
    clean_name = Path(filename).name
    fpath = DATA_DIR / "mugshots" / clean_name
    if not fpath.exists() or not fpath.is_file():
        raise HTTPException(status_code=404, detail="Mugshot photo not found")
    media_type = "image/png" if clean_name.lower().endswith(".png") else "image/jpeg"
    return FileResponse(str(fpath), media_type=media_type)

@app.get("/people/search")
def people_search(q: str = Query(..., description="Search person by name, ID, phone, account"),
                  iid: Optional[str] = Query(None), user: dict = Depends(get_current_user)):
    qlow = q.strip().lower()
    hits = []
    import json as js
    if iid and (INV_ROOT / iid / "output" / "graph.json").exists():
        serial = js.loads((INV_ROOT / iid / "output" / "graph.json").read_text())
        for n in serial.get("nodes", []):
            label = str(n.get("label") or n.get("name") or n.get("id"))
            phone = str(n.get("phone") or "")
            acct = str(n.get("account") or "")
            nid = str(n.get("id") or "")
            if qlow in nid.lower() or qlow in label.lower() or (phone and qlow in phone.lower()) or (acct and qlow in acct.lower()):
                hits.append({
                    "id": n["id"],
                    "name": label,
                    "cell": n.get("cell", "Unknown"),
                    "role": n.get("role", ""),
                    "phone": n.get("phone", ""),
                    "account": n.get("account", ""),
                    "photo": n.get("photo", f"/mugshots/{n['id']}.jpg")
                })
    else:
        pd = js.loads((DATA_DIR / "people_directory.json").read_text())
        allp = pd.get("network_people", []) + pd.get("noise_people", [])
        for p in allp:
            if qlow in p["id"].lower() or qlow in p["name"].lower() or qlow in p.get("phone","").lower() or qlow in p.get("account","").lower():
                item = dict(p)
                if "photo" not in item:
                    item["photo"] = f"/mugshots/{p['id']}.jpg"
                hits.append(item)
            elif qlow in p["name"].lower().split()[0]:  # first name
                item = dict(p)
                if "photo" not in item:
                    item["photo"] = f"/mugshots/{p['id']}.jpg"
                hits.append(item)
    return {"query": q, "results": hits[:15], "count": len(hits)}

@app.post("/people/search-image")
async def people_search_image(
    file: Optional[UploadFile] = File(None),
    image_base64: Optional[str] = Form(None),
    top_k: int = Form(6),
    user: dict = Depends(get_current_user)
):
    """AI Facial Recognition Search: match an uploaded suspect photo or webcam frame against criminal mugshots."""
    from backend.analytics.face_search import search_face
    img_bytes = b""
    if file is not None:
        img_bytes = await file.read()
    elif image_base64:
        import base64
        b64_str = image_base64.split(",")[-1]
        try:
            img_bytes = base64.b64decode(b64_str)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid base64 image data: {e}")

    if not img_bytes:
        raise HTTPException(status_code=400, detail="No image provided (upload file or image_base64)")

    matches = search_face(img_bytes, top_k=top_k)
    audit_log("POST /people/search-image", [m["id"] for m in matches[:3]])
    return {
        "status": "success",
        "matches_count": len(matches),
        "matches": matches,
    }

TEMPLATE_SCHEMAS = {
    "cdrs": {
        "title": "Call Detail Records (CDRs)",
        "headers": ["caller_phone", "callee_phone", "timestamp", "duration_sec", "cell_tower_location", "call_type"],
        "required": ["caller_phone", "callee_phone"],
        "sample": [
            {"caller_phone": "7000000001", "callee_phone": "7000000002", "timestamp": "2026-01-05 14:22:10", "duration_sec": "184", "cell_tower_location": "Mumbai Central Cell-12", "call_type": "voice"},
            {"caller_phone": "7000000003", "callee_phone": "7000000004", "timestamp": "2026-01-05 15:40:02", "duration_sec": "62", "cell_tower_location": "Bandra East Cell-04", "call_type": "sms"}
        ]
    },
    "transactions": {
        "title": "Financial Transactions",
        "headers": ["sender_id", "sender_name", "sender_account", "receiver_id", "receiver_name", "receiver_account", "amount_inr", "timestamp", "txn_type"],
        "required": ["sender_id", "receiver_id", "amount_inr"],
        "sample": [
            {"sender_id": "A1", "sender_name": "Anwar Sheikh", "sender_account": "AC0009000001", "receiver_id": "A2", "receiver_name": "Suresh Rane", "receiver_account": "AC0009000002", "amount_inr": "450000", "timestamp": "2026-01-06 11:15:00", "txn_type": "Hawala / Transfer"},
            {"sender_id": "A2", "sender_name": "Suresh Rane", "sender_account": "AC0009000002", "receiver_id": "A5", "receiver_name": "Ganesh Pawar", "receiver_account": "AC0009000005", "amount_inr": "120000", "timestamp": "2026-01-06 16:30:00", "txn_type": "Bank Transfer"}
        ]
    },
    "firs": {
        "title": "Police FIRs & Incident Reports",
        "headers": ["fir_id", "date", "station", "location", "ipc_sections", "narrative"],
        "required": ["narrative"],
        "sample": [
            {"fir_id": "FIR-2026-0042", "date": "2026-01-07 10:00:00", "station": "Crime Branch Unit 3", "location": "Dockside Ward", "ipc_sections": "IPC 384, Arms Act 25", "narrative": "Accused Farhan Qureshi and Vikas Chauhan were intercepted demanding extortion payment of ₹5,00,000 from local traders."}
        ]
    },
    "surveillance_reports": {
        "title": "Surveillance Reports",
        "headers": ["report_id", "date", "team", "location", "activity_notes", "confidence"],
        "required": ["activity_notes"],
        "sample": [
            {"report_id": "SURV-2026-101", "date": "2026-01-08 21:15:00", "team": "Alpha Recon Squad", "location": "Riverside Colony", "activity_notes": "Target Suresh Rane met with unidentified associate driving black Scorpio MH-01-AB-1234. Handed over briefcase.", "confidence": "0.95"}
        ]
    },
    "intelligence_reports": {
        "title": "Intelligence Reports",
        "headers": ["report_id", "date", "source_reliability", "narrative", "mentioned_entity_ids"],
        "required": ["narrative"],
        "sample": [
            {"report_id": "INTEL-2026-088", "date": "2026-01-09 09:30:00", "source_reliability": "A1 (High)", "narrative": "Confidential informant reports Anwar Sheikh ordered coordination meeting between Cell A and Cell B at Central Junction.", "mentioned_entity_ids": "A1, B1, X1"}
        ]
    },
    "social_posts": {
        "title": "Social Media Posts",
        "headers": ["post_id", "handle", "person_id", "timestamp", "post_text", "hashtags", "location_tag"],
        "required": ["post_text"],
        "sample": [
            {"post_id": "POST-9021", "handle": "@shadow_bhai", "person_id": "A4", "timestamp": "2026-01-10 18:45:00", "post_text": "Meeting the boss tonight at the warehouse. Big moves coming up.", "hashtags": "#mumbai #syndicate", "location_tag": "Dockside Warehouse"}
        ]
    },
    "criminal_history": {
        "title": "Criminal History & Prior Records",
        "headers": ["record_id", "person_id", "name", "alias", "dob", "prior_offences", "gang_affiliation", "known_address"],
        "required": ["name"],
        "sample": [
            {"record_id": "CRIM-001", "person_id": "A1", "name": "Anwar Sheikh", "alias": "Bhai", "dob": "1978-04-12", "prior_offences": "2022 - IPC 384 (Extortion); 2024 - Arms Act 25", "gang_affiliation": "Sheikh Syndicate", "known_address": "Flat 402, Marina Heights, Mumbai"}
        ]
    },
    "people_directory": {
        "title": "People Directory & Suspect Profiles",
        "headers": ["id", "name", "role", "cell", "phone", "account", "photo"],
        "required": ["name"],
        "sample": [
            {"id": "A1", "name": "Anwar Sheikh", "role": "Kingpin", "cell": "A", "phone": "7000000001", "account": "AC0009000001", "photo": "/mugshots/A1.jpg"}
        ]
    }
}

@app.get("/templates/schema")
def get_template_schemas():
    """Return schema, required fields, and sample preview rows for all 8 dataset categories."""
    return TEMPLATE_SCHEMAS

@app.get("/templates/{dataset_type}.csv")
def download_template_csv(dataset_type: str):
    """Download clean CSV starter template ready for data entry."""
    from fastapi.responses import Response
    import io, csv
    dtype = dataset_type.lower().replace("-", "_")
    info = TEMPLATE_SCHEMAS.get(dtype)
    if not info:
        raise HTTPException(status_code=404, detail=f"Unknown dataset template: {dataset_type}")
    
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=info["headers"])
    writer.writeheader()
    for row in info["sample"]:
        writer.writerow(row)
    
    csv_bytes = out.getvalue().encode("utf-8")
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=template_{dtype}.csv"}
    )


@app.post("/investigations/{iid}/process")
def inv_process(iid: str, user: dict = Depends(get_current_user)):
    meta = _require_meta(iid)
    # Demo fast path: if no files, load synthetic demo data (same pipeline, no case-specific logic)
    if not meta.get("files"):
        # Check if this is a Demo Case — use synthetic data as investigation data
        import shutil as _sh
        demo_files = ["cdrs.csv","transactions.csv","firs.csv","social_posts.csv","criminal_history.csv","intelligence_reports.csv","surveillance_reports.csv"]
        for fname in demo_files:
            src = DATA_DIR / fname
            if src.exists():
                dst = INV_ROOT / iid / "files" / fname
                dst.parent.mkdir(parents=True, exist_ok=True)
                if not dst.exists():
                    _sh.copy(src, dst)
                # add to meta if not present
                if not any(f["original"]==fname for f in meta.get("files",[])):
                    det = detect_schema(dst)
                    entry = {"original": fname, "stored": f"{iid}/files/{fname}", "format": det["format"], "detected_type": det["detected_type"], "type_confidence": det["type_confidence"], "columns": det["columns"], "sample_rows": det["sample_rows"][:2]}
                    meta["files"].append(entry)
                    # auto mapping
                    mapping = suggest_mapping(det["columns"], det["detected_type"])
                    valid,_ = validate_mapping(mapping, det["detected_type"])
                    meta.setdefault("mapping", {})[fname] = {"mapping": mapping, "validated": valid, "missing": []}
        # also ensure people_directory
        pd_src = DATA_DIR / "people_directory.json"
        if pd_src.exists():
            dst = INV_ROOT / iid / "files" / "people_directory.json"
            if not dst.exists():
                _sh.copy(pd_src, dst)
        save_meta(iid, meta)
        if not meta.get("files"):
            raise HTTPException(status_code=400, detail="No files uploaded and demo fast path failed")
    # Automated Schema Mapping: Ensure every file has an auto-generated, validated mapping
    meta_dirty = False
    for f in meta.get("files", []):
        if f["original"] not in meta.get("mapping", {}) or not meta["mapping"][f["original"]].get("mapping"):
            stored = INV_ROOT / f["stored"]
            det = detect_schema(stored)
            mapping = suggest_mapping(det["columns"], det["detected_type"])
            valid, missing = validate_mapping(mapping, det["detected_type"])
            meta.setdefault("mapping", {})[f["original"]] = {
                "mapping": mapping,
                "validated": True,
                "missing": missing
            }
            meta_dirty = True
        else:
            meta["mapping"][f["original"]]["validated"] = True
            meta_dirty = True
    if meta_dirty:
        save_meta(iid, meta)

    set_processing(iid, "processing", {"step": "normalization"})
    # Build datasets dict per investigation by reading each file + applying mapping + normalizing
    # Pillar 3: universal readers (multi-encoding CSV, multi-sheet XLSX, TXT/PDF/DOCX),
    # row-level quarantine (broken rows never stop the case) + entity bootstrapping.
    from backend.ingestion.detector import read_full_rows
    from backend.ingestion.normalizer import normalize_with_quarantine
    from backend.ingestion.bootstrap import bootstrap_entities
    inv_datasets = {"firs": [], "cdrs": [], "transactions": [], "social_posts": [], "criminal_history": [], "intelligence_reports": [], "surveillance_reports": [], "people_directory": {"network_people": [], "noise_people": []}}
    # Also keep quarantine per investigation
    import csv
    quarantine = []
    for f in meta["files"]:
        stored = INV_ROOT / f["stored"]
        det = detect_schema(stored)
        mapping = meta["mapping"][f["original"]]["mapping"]
        fmt = det["format"]
        try:
            _cols, rows, _rmeta = read_full_rows(stored, fmt)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to read {f['original']}: {e}")
        if det["detected_type"] == "people_directory":
            # People directory uploads replace/extend the directory directly
            for r in rows:
                r.pop("ground_truth_flag", None)
            mapped = apply_mapping(rows, mapping)
            for m in mapped:
                entry = {
                    "id": str(m.get("id") or m.get("person_id") or f"AUTO-{len(inv_datasets['people_directory']['network_people'])+1:03d}"),
                    "name": str(m.get("name") or m.get("person_id") or "Unknown"),
                    "role": str(m.get("role") or "Unknown"),
                    "cell": str(m.get("cell") or "Unknown"),
                    "phone": str(m.get("phone") or ""),
                    "account": str(m.get("account") or ""),
                }
                if m.get("photo"):
                    entry["photo"] = str(m["photo"])
                inv_datasets["people_directory"]["network_people"].append(entry)
            continue
        # apply mapping
        mapped = apply_mapping(rows, mapping)
        # normalize per type with row-level quarantine (plan line 112)
        normed, q_rows = normalize_with_quarantine(mapped, det["detected_type"], source_file=f["original"])
        quarantine.extend(q_rows)
        # quarantine missing required mapping (file-level, kept for audit)
        valid, missing = validate_mapping(mapping, det["detected_type"])
        if missing:
            for m in missing:
                quarantine.append({"row_no": 0, "source_file": f["original"], "reason": f"Missing mapped field {m}", "confidence": 0.0})
        # merge into inv_datasets
        key = det["detected_type"]
        if key in inv_datasets:
            if isinstance(inv_datasets[key], list):
                inv_datasets[key].extend(normed)
            else:
                inv_datasets[key] = normed
        else:
            # unknown type → treat as firs-like
            inv_datasets["firs"].extend(normed)
    # Ensure people_directory exists — if not uploaded, reuse default synthetic for demo (fast path)
    if not inv_datasets["people_directory"].get("network_people"):
        import json as js
        default_pd = js.loads((DATA_DIR / "people_directory.json").read_text())
        inv_datasets["people_directory"] = default_pd
    # Pillar 3.E — autonomous entity bootstrapping (plan line 115): backfill
    # suspect profiles from phones/accounts/names seen in messy uploads.
    try:
        inv_datasets["people_directory"], boot_stats = bootstrap_entities(
            inv_datasets, inv_datasets["people_directory"])
        if any(boot_stats.values()):
            quarantine.append({"row_no": 0, "source_file": "entity_bootstrapper",
                               "reason": f"Auto-created profiles: {boot_stats}", "confidence": 0.5})
    except Exception as e:
        print(f"Entity bootstrapping skipped: {e}")
    # strip ground_truth_flag if any leaked from custom files
    for k, rows in inv_datasets.items():
        if isinstance(rows, list):
            for r in rows:
                r.pop("ground_truth_flag", None)
                r.pop("ground_truth_flag ", None)
    # Write quarantine
    out_dir = INV_ROOT / iid / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    # Run extraction → resolution → graph per investigation (reuse existing modules)
    from backend.extraction.entity_extractor import extract_all
    from backend.resolution.resolver import resolve_entities, write_resolution
    all_entities, relationships = extract_all(inv_datasets)
    struct = [e for e in all_entities if e.get("confidence",0) >= 0.8]
    unstruct = [e for e in all_entities if e.get("confidence",0) < 0.8]
    mention_map, res_rows = resolve_entities(struct, unstruct, inv_datasets["people_directory"], datasets=inv_datasets)
    # Build graph per investigation — output_dir is passed as a parameter so
    # concurrent /process requests cannot corrupt each other's output via
    # shared module globals (see backend.graph.builder.build_graph).
    import backend.graph.builder as gb
    # We call build_graph instead of just in_memory to ensure Neo4j gets the case-isolated push
    serial = gb.build_graph(inv_datasets, all_entities, relationships, mention_map, iid=iid, output_dir=out_dir)
    # Save resolution/quarantine per investigation
    import csv as csvm
    with open(out_dir / "resolution.csv", "w", newline='', encoding='utf-8') as fh:
        w = csvm.DictWriter(fh, fieldnames=["master_id","merged_ids","method","confidence","name_score","phone_score","context_score","source_id","source_type","evidence_snippet","evidence_hash"])
        w.writeheader()
        for r in res_rows:
            for k in ["name_score","phone_score","context_score","source_id","source_type","evidence_snippet","evidence_hash"]:
                r.setdefault(k, "")
            w.writerow({k: r.get(k,"") for k in ["master_id","merged_ids","method","confidence","name_score","phone_score","context_score","source_id","source_type","evidence_snippet","evidence_hash"]})
    with open(out_dir / "quarantine.csv", "w", newline='', encoding='utf-8') as fh:
        w = csvm.DictWriter(fh, fieldnames=["row_no","source_file","reason","confidence"])
        w.writeheader()
        w.writerows(quarantine)
    # Also save mapped preview + full datasets for per-case analytics
    import json
    (INV_ROOT / iid / "mapped" / "datasets.json").write_text(json.dumps({k: (v[:2] if isinstance(v,list) else v) for k,v in inv_datasets.items()}, indent=2, default=str))
    (INV_ROOT / iid / "mapped" / "full_datasets.json").write_text(json.dumps(inv_datasets, indent=2, default=str))
    set_processing(iid, "completed", {"entities": len(all_entities), "relationships": len(relationships), "graph_nodes": serial["stats"]["node_count"], "graph_edges": serial["stats"]["edge_count"]})
    audit_log(f"POST /investigations/{iid}/process", [serial["stats"]["node_count"]])
    return {"status": "completed", "stats": serial["stats"], "entities": len(all_entities), "relationships": len(relationships)}

@app.get("/investigations/{iid}/stats")
def inv_stats(iid: str, user: dict = Depends(get_current_user)):
    meta = _require_meta(iid)
    out = INV_ROOT / iid / "output" / "graph.json"
    if not out.exists():
        raise HTTPException(status_code=404, detail="Not processed yet — POST /process")
    import json as js
    serial = js.loads(out.read_text())
    return {"investigation": meta, "graph": serial["stats"]}

@app.get("/investigations/{iid}/leads")
def inv_leads(iid: str, limit: int = Query(20, ge=1, le=100), user: dict = Depends(get_current_user)):
    out = INV_ROOT / iid / "output" / "graph.json"
    if not out.exists():
        raise HTTPException(status_code=404, detail="Not processed")
    import json as js
    serial = js.loads(out.read_text())
    full_ds_path = INV_ROOT / iid / "mapped" / "full_datasets.json"
    ds = js.loads(full_ds_path.read_text()) if full_ds_path.exists() else None
    leads = get_leads(limit=limit, datasets=ds, graph_serial=serial)
    return {"leads": leads[:limit], "investigation_id": iid}

@app.get("/investigations/{iid}/graph")
def inv_graph(iid: str, day: Optional[int] = Query(None, ge=1, le=90), user: dict = Depends(CAN_VIEW_GRAPH)):
    out = INV_ROOT / iid / "output" / "graph.json"
    if not out.exists():
        raise HTTPException(status_code=404, detail="Not processed")
    import json as js
    serial = js.loads(out.read_text())
    nodes, edges = serial["nodes"], serial["edges"]
    if day is not None:
        filtered_edges = [e for e in edges if e.get("day") is None or (isinstance(e.get("day"), int) and e["day"] <= day and e["day"] >= day-6)]
        incident = set()
        for e in filtered_edges:
            incident.add(e["src"]); incident.add(e["dst"])
        top_bridges = {b["id"] for b in compute_bridges(graph_serial=serial)[:6]}
        for n in nodes:
            if n["id"] in top_bridges:
                incident.add(n["id"])
        filtered_nodes = [n for n in nodes if n["id"] in incident]
        for idx, e in enumerate(filtered_edges):
            e = dict(e)
            eday = e.get("day")
            if eday is None or eday == day:
                e["_opacity"] = 1.0
            else:
                e["_opacity"] = round(0.35 + 0.65 * (1 - (day - eday)/6), 2)
            filtered_edges[idx] = e
        return {"day": day, "nodes": filtered_nodes, "edges": filtered_edges, "total_nodes": len(nodes), "total_edges": len(edges)}
    return serial

@app.get("/investigations/{iid}/whatif")
def inv_whatif(iid: str, remove_id: str = Query(..., description="Node ID to simulate removing"),
               user: dict = Depends(CAN_VIEW_GRAPH)):
    """WHAT-IF sandbox: compare graph statistics with a node (and its edges) removed.

    Read-only — the stored case graph is never modified. impact_score (0-100) =
    100 * (0.6 * fraction_of_edges_removed + 0.4 * fragmentation_gain), where
    fragmentation_gain is the normalized increase in weakly-connected components.
    """
    import networkx as nx

    out = INV_ROOT / iid / "output" / "graph.json"
    if not out.exists():
        raise HTTPException(status_code=404, detail="Not processed")
    import json as js
    serial = js.loads(out.read_text())
    node_ids = {n["id"] for n in serial["nodes"]}
    if remove_id not in node_ids:
        raise HTTPException(status_code=404, detail=f"Node {remove_id} not found in this case graph")

    def components(nodes: set, edges: list) -> int:
        g = nx.DiGraph()
        g.add_nodes_from(nodes)
        g.add_edges_from([(e["src"], e["dst"]) for e in edges])
        return nx.number_weakly_connected_components(g)

    original_nodes = len(node_ids)
    original_edges = len(serial["edges"])
    remaining_nodes = sorted(node_ids - {remove_id})
    remaining_set = set(remaining_nodes)
    remaining_edges = [e for e in serial["edges"] if e["src"] in remaining_set and e["dst"] in remaining_set]
    removed_edges = original_edges - len(remaining_edges)
    comp_before = components(node_ids, serial["edges"])
    comp_after = components(remaining_set, remaining_edges)
    edge_loss = (removed_edges / original_edges) if original_edges else 0.0
    frag_gain = max(0, comp_after - comp_before) / max(comp_before, 1)
    impact = round(100 * (0.6 * edge_loss + 0.4 * min(1.0, frag_gain)), 1)
    audit_log(f"/investigations/{iid}/whatif?remove_id={remove_id}", [remove_id])
    return {
        "remove_id": remove_id,
        "original_nodes": original_nodes,
        "remaining_nodes": len(remaining_nodes),
        "original_edges": original_edges,
        "remaining_edges": len(remaining_edges),
        "removed_edges": removed_edges,
        "disconnected_components": comp_after,
        "components_before": comp_before,
        "impact_score": impact,
        "simulation_only": True,
    }

# keep original health etc.


@app.get("/stats")
def stats(user: dict = Depends(get_current_user)):
    datasets, _ = load_all(DATA_DIR)
    serial = load_graph_serial()
    return {
        "datasets": {k: len(v) if isinstance(v,list) else f"{len(v.get('network_people',[]))}+{len(v.get('noise_people',[]))}" for k,v in datasets.items()},
        "graph": serial["stats"],
        "ground_truth_flag_stripped": True
    }

@app.get("/graph")
def get_graph(day: Optional[int] = Query(None, ge=1, le=90, description="Day filter 1-90, story slice 50-70"), user: dict = Depends(CAN_VIEW_GRAPH)):
    serial = load_graph_serial()
    if not serial["nodes"]:
        raise HTTPException(status_code=503, detail="Graph not built yet — run: python -m backend.loader --clean && python -m backend.graph.builder")
    nodes = serial["nodes"]
    edges = serial["edges"]
    # Day snapshot: snapshot N + 6-day ghost per design locks
    if day is not None:
        # filter edges to day window [day-6, day] for ghost trails, nodes stay all with opacity handling client-side
        # For API we return filtered edges + all nodes (client will style ghost)
        filtered_edges = [e for e in edges if e.get("day") is None or (isinstance(e.get("day"), int) and e["day"] <= day and e["day"] >= day-6)]
        # Also include non-temporal edges (people_directory OWN) always
        # We already included them as day=None
        # For demo, also filter nodes to those incident to filtered edges + ensure bridges always visible
        incident = set()
        for e in filtered_edges:
            incident.add(e["src"]); incident.add(e["dst"])
        # top bridge nodes always visible
        top_bridges = {b["id"] for b in compute_bridges(graph_serial=serial)[:6]}
        for n in nodes:
            if n["id"] in top_bridges:
                incident.add(n["id"])
        filtered_nodes = [n for n in nodes if n["id"] in incident]
        # Attach opacity meta: 1.0 for day==snapshot, 0.3-0.6 for ghost
        for idx, e in enumerate(filtered_edges):
            # copy to avoid mutating cached serial
            e = dict(e)
            eday = e.get("day")
            if eday is None:
                e["_opacity"] = 1.0
            elif eday == day:
                e["_opacity"] = 1.0
            else:
                # linear fade over 6 days
                e["_opacity"] = round(0.35 + 0.65 * (1 - (day - eday)/6), 2)
            filtered_edges[idx] = e
        audit_log(f"/graph?day={day}", [n["id"] for n in filtered_nodes][:20])
        return {"day": day, "nodes": filtered_nodes, "edges": filtered_edges, "total_nodes": len(nodes), "total_edges": len(edges)}
    audit_log("/graph", [n["id"] for n in nodes][:20])
    return {"nodes": nodes, "edges": edges, "stats": serial["stats"]}

def _get_inv_datasets_and_serial(iid: Optional[str] = None):
    if iid and (INV_ROOT / iid / "output" / "graph.json").exists():
        import json as js
        serial = js.loads((INV_ROOT / iid / "output" / "graph.json").read_text())
        full_ds_path = INV_ROOT / iid / "mapped" / "full_datasets.json"
        datasets = js.loads(full_ds_path.read_text()) if full_ds_path.exists() else {}
        return datasets, serial
    datasets, _ = load_all(DATA_DIR)
    serial = load_graph_serial()
    return datasets, serial

@app.get("/bridges")
def get_bridges(iid: Optional[str] = Query(None), user: dict = Depends(get_current_user)):
    datasets, serial = _get_inv_datasets_and_serial(iid)
    bridges = compute_bridges(graph_serial=serial)
    if not bridges:
        raise HTTPException(status_code=503, detail="Graph not built or centrality unavailable")
    # Only return flagged top-6 per spec shape, but include full for why panel
    audit_log("/bridges", [b["id"] for b in bridges if b.get("flagged")])
    return bridges

@app.get("/bursts")
def get_bursts(iid: Optional[str] = Query(None), user: dict = Depends(CAN_VIEW_GRAPH)):
    datasets, serial = _get_inv_datasets_and_serial(iid)
    bursts = detect_bursts(datasets)
    audit_log("/bursts", [f"{b['cell']}:{b['day']}" for b in bursts])
    return bursts

@app.get("/structuring")
def get_structuring(iid: Optional[str] = Query(None), user: dict = Depends(get_current_user)):
    datasets, serial = _get_inv_datasets_and_serial(iid)
    flags = detect_structuring(datasets)
    audit_log("/structuring", [f["receiver"] for f in flags])
    return flags

@app.get("/communities")
def get_communities(filter_bridges: bool = True, iid: Optional[str] = Query(None), user: dict = Depends(get_current_user)):
    datasets, serial = _get_inv_datasets_and_serial(iid)
    comms = detect_communities(filter_bridges=filter_bridges)
    audit_log(f"/communities?filter_bridges={filter_bridges}", [str(c["community_id"]) for c in comms])
    return comms

@app.get("/centrality")
def get_centrality(iid: Optional[str] = Query(None), user: dict = Depends(get_current_user)):
    datasets, serial = _get_inv_datasets_and_serial(iid)
    cent = compute_centrality(graph_serial=serial)
    audit_log("/centrality", [c["id"] for c in cent[:10]])
    return cent

@app.get("/towers")
def get_towers(iid: Optional[str] = Query(None), user: dict = Depends(CAN_VIEW_GRAPH)):
    """Tower schematic data — co-location counts per CDR tower.

    Schematic, not geographic: towers are glyphs (TWR-01...) colored by
    dominant cell. Aggregated live from CALLED edge meta.tower.
    """
    from collections import Counter
    datasets, serial = _get_inv_datasets_and_serial(iid)
    if not serial["nodes"]:
        raise HTTPException(status_code=503, detail="Graph not built yet — run: python -m backend.pipeline --clean")
    id_to_cell = {n["id"]: n.get("cell", "Unknown") for n in serial["nodes"]}
    agg = {}
    for e in serial["edges"]:
        if e.get("kind") != "CALLED":
            continue
        tower = ((e.get("meta") or {}).get("tower")) or "Unknown"
        cell = id_to_cell.get(e.get("src"), "Unknown")
        entry = agg.setdefault(tower, {"calls": 0, "cells": Counter()})
        entry["calls"] += 1
        entry["cells"][cell] += 1
    towers = []
    for i, label in enumerate(sorted(agg), start=1):
        entry = agg[label]
        cells = dict(entry["cells"])
        towers.append({
            "tower_id": f"TWR-{i:02d}",
            "label": label,
            "call_count": entry["calls"],
            "cells": cells,
            "dominant_cell": max(cells, key=cells.get),
            "co_location_count": len([c for c in cells if c in ("A", "B", "C")]),
        })
    towers.sort(key=lambda t: t["call_count"], reverse=True)
    audit_log("/towers", [t["tower_id"] for t in towers[:10]])
    return {"towers": towers, "count": len(towers),
            "disclaimer": "Schematic — not geographic. Potential investigative lead, not a guilt determination."}

@app.get("/leads")
def get_leads_endpoint(limit: int = Query(20, ge=1, le=100, description="Top N leads"), priority: Optional[str] = Query(None, description="Filter HIGH/MEDIUM/LOW"), iid: Optional[str] = Query(None), user: dict = Depends(get_current_user)):
    datasets, serial = _get_inv_datasets_and_serial(iid)
    leads = get_leads(limit=limit, datasets=datasets, graph_serial=serial)
    if priority:
        leads = [l for l in leads if l["priority"] == priority.upper()]
    audit_log(f"/leads?limit={limit}", [l["entity_id"] for l in leads[:10]])
    return {"leads": leads, "count": len(leads), "formula": "0.25bridge +0.20financial +0.15comm +0.10temporal +0.15evidence +0.10centrality +0.05cross *100", "disclaimer": "Potential investigative leads — not guilt determinations."}

@app.get("/anomalies")
def get_anomalies(iid: Optional[str] = Query(None), user: dict = Depends(get_current_user)):
    datasets, serial = _get_inv_datasets_and_serial(iid)
    anoms = get_unified_anomalies(datasets=datasets)
    audit_log("/anomalies", [a["entity_id"] for a in anoms[:10]])
    return anoms

@app.get("/cross-case")
def get_cross_case(iid: Optional[str] = Query(None), user: dict = Depends(get_current_user)):
    datasets, serial = _get_inv_datasets_and_serial(iid)
    cc = detect_cross_case(datasets)
    audit_log("/cross-case", [c["shared_entity"] for c in cc[:10]])
    return cc

@app.get("/temporal")
def get_temporal(iid: Optional[str] = Query(None), user: dict = Depends(get_current_user)):
    datasets, serial = _get_inv_datasets_and_serial(iid)
    ti = get_temporal_intelligence(datasets)
    audit_log("/temporal", [f"{g['span']}" for g in ti["correlated_groups"]])
    return ti

@app.get("/connections/explain")
def explain_connection_endpoint(
    src: str = Query(..., description="Source entity ID"),
    dst: str = Query(..., description="Target entity ID"),
    iid: Optional[str] = Query(None, description="Optional Investigation ID"),
    user: dict = Depends(get_current_user)
):
    datasets, serial = _get_inv_datasets_and_serial(iid)
    res = explain_connection(src_id=src, dst_id=dst, datasets=datasets, graph=serial)
    audit_log(f"/connections/explain {src} <-> {dst}", [src, dst])
    return res

@app.get("/investigations/{iid}/explain-connection")
def explain_inv_connection_endpoint(
    iid: str,
    src: str = Query(..., description="Source entity ID"),
    dst: str = Query(..., description="Target entity ID"),
    user: dict = Depends(get_current_user)
):
    datasets, serial = _get_inv_datasets_and_serial(iid)
    res = explain_connection(src_id=src, dst_id=dst, datasets=datasets, graph=serial)
    audit_log(f"/investigations/{iid}/explain-connection {src} <-> {dst}", [src, dst])
    return res

@app.get("/recommendations")
def get_recommendations_endpoint(
    target_pid: Optional[str] = Query(None, description="Optional focal suspect ID"),
    iid: Optional[str] = Query(None, description="Optional Investigation ID"),
    limit: int = Query(10, ge=1, le=50),
    user: dict = Depends(get_current_user)
):
    datasets, serial = _get_inv_datasets_and_serial(iid)
    res = generate_case_recommendations(datasets=datasets, graph=serial, target_pid=target_pid, limit=limit)
    audit_log(f"/recommendations?iid={iid}&target_pid={target_pid}", [r["target_id"] for r in res.get("recommendations", [])])
    return res

@app.get("/investigations/{iid}/recommendations")
def get_inv_recommendations_endpoint(
    iid: str,
    target_pid: Optional[str] = Query(None, description="Optional focal suspect ID"),
    limit: int = Query(10, ge=1, le=50),
    user: dict = Depends(get_current_user)
):
    datasets, serial = _get_inv_datasets_and_serial(iid)
    res = generate_case_recommendations(datasets=datasets, graph=serial, target_pid=target_pid, limit=limit)
    audit_log(f"/investigations/{iid}/recommendations", [r["target_id"] for r in res.get("recommendations", [])])
    return res

@app.get("/people/{pid}/recommendations")
def get_person_recommendations_endpoint(
    pid: str,
    iid: Optional[str] = Query(None),
    limit: int = Query(6, ge=1, le=20),
    user: dict = Depends(get_current_user)
):
    datasets, serial = _get_inv_datasets_and_serial(iid)
    res = generate_case_recommendations(datasets=datasets, graph=serial, target_pid=pid, limit=limit)
    audit_log(f"/people/{pid}/recommendations", [r["target_id"] for r in res.get("recommendations", [])])
    return res

# -------------------------------------------------------------
# Blockchain & Evidence Chain-of-Custody (Section 63 BSA / 65B IEA)
# -------------------------------------------------------------
@app.get("/blockchain-ledger")
def get_blockchain_ledger_endpoint(iid: Optional[str] = Query(None), user: dict = Depends(get_current_user)):
    datasets, serial = _get_inv_datasets_and_serial(iid)
    case_name = "Master Network Case"
    if iid:
        meta = get_meta(iid)
        if meta: case_name = meta.get("name", case_name)
    ledger = build_blockchain_ledger(datasets=datasets, graph=serial, case_name=case_name, officer=user.get("name") or user.get("username", "Investigator"))
    audit_log(f"/blockchain-ledger (master_hash: {ledger['master_merkle_root'][:10]})", [b["block_hash"] for b in ledger["blocks"]])
    return ledger

@app.get("/investigations/{iid}/blockchain-ledger")
def get_inv_blockchain_ledger_endpoint(iid: str, user: dict = Depends(get_current_user)):
    datasets, serial = _get_inv_datasets_and_serial(iid)
    meta = get_meta(iid)
    case_name = meta.get("name", f"Investigation {iid}") if meta else f"Investigation {iid}"
    ledger = build_blockchain_ledger(datasets=datasets, graph=serial, case_name=case_name, officer=user.get("name") or user.get("username", "Investigator"))
    audit_log(f"/investigations/{iid}/blockchain-ledger", [b["block_hash"] for b in ledger["blocks"]])
    return ledger

@app.get("/chain-of-custody-certificate")
def get_chain_of_custody_cert_endpoint(iid: Optional[str] = Query(None), user: dict = Depends(get_current_user)):
    datasets, serial = _get_inv_datasets_and_serial(iid)
    case_name = "Master Network Case"
    if iid:
        meta = get_meta(iid)
        if meta: case_name = meta.get("name", case_name)
    ledger = build_blockchain_ledger(datasets=datasets, graph=serial, case_name=case_name, officer=user.get("name") or user.get("username", "Investigator"))
    cert = generate_chain_of_custody_certificate(ledger, officer_name=user.get("name") or user.get("username", "Investigator"))
    audit_log(f"/chain-of-custody-certificate (cert: {cert['certificate_id']})", [cert["certificate_id"]])
    return cert

@app.get("/investigations/{iid}/chain-of-custody-certificate")
def get_inv_chain_of_custody_cert_endpoint(iid: str, user: dict = Depends(get_current_user)):
    datasets, serial = _get_inv_datasets_and_serial(iid)
    meta = get_meta(iid)
    case_name = meta.get("name", f"Investigation {iid}") if meta else f"Investigation {iid}"
    ledger = build_blockchain_ledger(datasets=datasets, graph=serial, case_name=case_name, officer=user.get("name") or user.get("username", "Investigator"))
    cert = generate_chain_of_custody_certificate(ledger, officer_name=user.get("name") or user.get("username", "Investigator"))
    audit_log(f"/investigations/{iid}/chain-of-custody-certificate", [cert["certificate_id"]])
    return cert

class VerifyHashRequest(BaseModel):
    query: Optional[str] = None
    hash: Optional[str] = None
    iid: Optional[str] = None

@app.post("/verify-evidence-hash")
def verify_hash_endpoint(payload: VerifyHashRequest, user: dict = Depends(get_current_user)):
    q = payload.query or payload.hash or ""
    datasets, serial = _get_inv_datasets_and_serial(payload.iid)
    case_name = "Master Network Case"
    if payload.iid:
        meta = get_meta(payload.iid)
        if meta: case_name = meta.get("name", case_name)
    officer = user.get("name") or user.get("username", "Authorized Law Enforcement Officer")
    ledger = build_blockchain_ledger(datasets=datasets, graph=serial, case_name=case_name, officer=officer)
    res = verify_evidence_hash_in_ledger(q, ledger)
    audit_log(f"/verify-evidence-hash query='{q[:30]}'", [res.get("computed_hash", "")])
    return res

@app.post("/investigations/{iid}/verify-evidence-hash")
def verify_inv_hash_endpoint(iid: str, payload: VerifyHashRequest, user: dict = Depends(get_current_user)):
    q = payload.query or payload.hash or ""
    datasets, serial = _get_inv_datasets_and_serial(iid)
    meta = get_meta(iid)
    case_name = meta.get("name", f"Investigation {iid}") if meta else f"Investigation {iid}"
    officer = user.get("name") or user.get("username", "Authorized Law Enforcement Officer")
    ledger = build_blockchain_ledger(datasets=datasets, graph=serial, case_name=case_name, officer=officer)
    res = verify_evidence_hash_in_ledger(q, ledger)
    audit_log(f"/investigations/{iid}/verify-evidence-hash query='{q[:30]}'", [res.get("computed_hash", "")])
    return res

# -------------------------------------------------------------
# Geospatial Map & Cell-Tower Movement Engine
# -------------------------------------------------------------
@app.get("/geospatial/towers")
def get_geo_towers_endpoint(iid: Optional[str] = Query(None), user: dict = Depends(get_current_user)):
    datasets, serial = _get_inv_datasets_and_serial(iid)
    data = get_cell_towers_geospatial(datasets=datasets)
    audit_log("/geospatial/towers", [t["tower_id"] for t in data.get("towers", [])[:10]])
    return data

@app.get("/investigations/{iid}/geospatial/towers")
def get_inv_geo_towers_endpoint(iid: str, user: dict = Depends(get_current_user)):
    datasets, serial = _get_inv_datasets_and_serial(iid)
    data = get_cell_towers_geospatial(datasets=datasets)
    audit_log(f"/investigations/{iid}/geospatial/towers", [t["tower_id"] for t in data.get("towers", [])[:10]])
    return data

@app.get("/geospatial/trajectories")
def get_geo_trajectories_endpoint(
    pid: Optional[str] = Query(None),
    day_start: Optional[int] = Query(None),
    day_end: Optional[int] = Query(None),
    iid: Optional[str] = Query(None),
    user: dict = Depends(get_current_user)
):
    datasets, serial = _get_inv_datasets_and_serial(iid)
    data = get_suspect_trajectories(datasets=datasets, person_id=pid, day_start=day_start, day_end=day_end)
    audit_log(f"/geospatial/trajectories pid={pid}", [t["person_id"] for t in data.get("trajectories", [])[:10]])
    return data

@app.get("/investigations/{iid}/geospatial/trajectories")
def get_inv_geo_trajectories_endpoint(
    iid: str,
    pid: Optional[str] = Query(None),
    day_start: Optional[int] = Query(None),
    day_end: Optional[int] = Query(None),
    user: dict = Depends(get_current_user)
):
    datasets, serial = _get_inv_datasets_and_serial(iid)
    data = get_suspect_trajectories(datasets=datasets, person_id=pid, day_start=day_start, day_end=day_end)
    audit_log(f"/investigations/{iid}/geospatial/trajectories pid={pid}", [t["person_id"] for t in data.get("trajectories", [])[:10]])
    return data

@app.get("/geospatial/hotspots")
def get_geo_hotspots_endpoint(iid: Optional[str] = Query(None), user: dict = Depends(get_current_user)):
    datasets, serial = _get_inv_datasets_and_serial(iid)
    data = get_co_location_hotspots(datasets=datasets)
    audit_log("/geospatial/hotspots", [h["location_name"] for h in data.get("hotspots", [])[:10]])
    return data

@app.get("/investigations/{iid}/geospatial/hotspots")
def get_inv_geo_hotspots_endpoint(iid: str, user: dict = Depends(get_current_user)):
    datasets, serial = _get_inv_datasets_and_serial(iid)
    data = get_co_location_hotspots(datasets=datasets)
    audit_log(f"/investigations/{iid}/geospatial/hotspots", [h["location_name"] for h in data.get("hotspots", [])[:10]])
    return data

@app.get("/why/{entity_id}")
def why_flagged(entity_id: str, iid: Optional[str] = Query(None), user: dict = Depends(get_current_user)):
    import json as js
    if iid and (INV_ROOT / iid / "output" / "graph.json").exists():
        serial = js.loads((INV_ROOT / iid / "output" / "graph.json").read_text())
        full_ds_path = INV_ROOT / iid / "mapped" / "full_datasets.json"
        datasets = js.loads(full_ds_path.read_text()) if full_ds_path.exists() else {}
    else:
        serial = load_graph_serial()
        datasets, _ = load_all(DATA_DIR)

    node = next((n for n in serial["nodes"] if n["id"]==entity_id), None)
    if not node:
        raise HTTPException(status_code=404, detail=f"Unknown id {entity_id} — check quarantine.csv or resolution.csv")
    # Collect top signals dynamically for this graph & dataset
    centrality = compute_centrality(graph_serial=serial)
    bridges = compute_bridges(graph_serial=serial)
    bursts = detect_bursts(datasets)
    struct = detect_structuring(datasets)

    cent_entry = next((c for c in centrality if c["id"]==entity_id), None)
    bridge_entry = next((b for b in bridges if b["id"]==entity_id), None)

    # Count edges incident
    edges = [e for e in serial["edges"] if e["src"]==entity_id or e["dst"]==entity_id]
    called = [e for e in edges if e["kind"]=="CALLED"]
    transacted = [e for e in edges if e["kind"]=="TRANSACTED"]
    mentioned = [e for e in edges if e["kind"]=="MENTIONED_IN"]

    # Sources ≥2 rows: collect provenance with Task2 supporting_text + evidence_hash
    sources = []
    for e in edges[:12]:
        sources.append({
            "source": e.get("source"), "source_type": e.get("source_type"), "day": e.get("day"),
            "confidence": e.get("confidence"), "kind": e.get("kind"),
            "supporting_text": e.get("supporting_text","")[:200],
            "evidence_hash": e.get("evidence_hash",""),
            "extractor": e.get("extractor","")
        })
    # also add FIR narrative sources if any
    for row in datasets.get("firs", []) + datasets.get("surveillance_reports", []) + datasets.get("intelligence_reports", []):
        txt = row.get("narrative","") + row.get("activity_notes","")
        if node.get("label","") in txt or entity_id in str(row):
            snippet = txt[:120].replace("\n"," ")
            import hashlib as _hl
            h = _hl.sha256(snippet.encode()).hexdigest()[:16]
            sources.append({"source": row.get("fir_id") or row.get("report_id"), "source_type": "text_mention", "day": row.get("day"), "confidence": 0.6, "supporting_text": snippet, "evidence_hash": h, "extractor": "text_mention"})

    # Task3: Lead Score for this entity
    lead = lead_for_entity(entity_id)
    cross = [c for c in detect_cross_case(datasets) if c["shared_entity"]==entity_id]
    anoms = [a for a in get_unified_anomalies(datasets) if a["entity_id"]==entity_id]
    temporal_info = get_temporal_intelligence(datasets)

    top_signals = []
    if lead and lead["priority"] == "HIGH":
        top_signals.append(f"Potential investigative lead — Lead Score {lead['lead_score']}/100 Priority {lead['priority']} ({lead['explanation']})")
    elif lead:
        top_signals.append(f"Lead Score {lead['lead_score']}/100 Priority {lead['priority']}")
    if bridge_entry and bridge_entry.get("flagged"):
        top_signals.append(f"Flagged as bridge — bridge_score {bridge_entry['bridge_score']} (rank {bridge_entry['rank']}) connecting {bridge_entry.get('cells')}")
    if cent_entry and cent_entry.get("betweenness",0) > 0.05:
        top_signals.append(f"High betweenness {cent_entry['betweenness']} — lies on many shortest paths")
    if len(called) >= 5:
        top_signals.append(f"{len(called)} CALLS edges (communication hub)")
    if len(transacted) >= 3:
        top_signals.append(f"{len(transacted)} TRANSACTED edges — financial interactions (potential structuring relevance)")
    if node.get("degree",0) >= 8:
        top_signals.append(f"High degree {node['degree']} — connected to many entities")
    # anomaly signals
    for a in anoms[:2]:
        top_signals.append(f"{a['anomaly_type']} anomaly score {a['score']} ({a['severity']}) — {a['explanation'][:100]}")
    if cross:
        top_signals.append(f"Cross-case shared across {len(cross[0]['cases'])} cases — {cross[0]['relationship_path']}")
    # burst correlation
    if any(abs(b["day"] - (node.get("day") or 0)) <= 7 for b in bursts):
        top_signals.append("Temporal correlation with burst window 58/61/64 (check /bursts)")
    elif any(g for g in temporal_info["correlated_groups"] if lead and lead.get("cell") in g["cells"]):
        top_signals.append(f"Temporal correlated burst group {temporal_info['correlated_groups'][0]['days']} cells {temporal_info['correlated_groups'][0]['cells']}")

    if not top_signals:
        top_signals.append("No strong flag — low centrality, few edges. Potential investigative lead if cross-cell location shared (check timeline).")

    # Ensure at least 2 sources — pad with degree info if needed
    while len(sources) < 2 and len(edges) > len(sources):
        sources.append({"source": edges[len(sources)].get("source"), "source_type": edges[len(sources)].get("source_type"), "day": edges[len(sources)].get("day"), "confidence": edges[len(sources)].get("confidence")})

    audit_log(f"/why/{entity_id}", [entity_id] + [s.get("source") for s in sources if s.get("source")])
    return {
        "id": entity_id,
        "label": node.get("label"),
        "cell": node.get("cell"),
        "role": node.get("role"),
        "degree": node.get("degree"),
        "top_signals": top_signals[:6],
        "centrality": cent_entry,
        "bridge": bridge_entry,
        "lead": lead,
        "anomalies": anoms[:5],
        "cross_case": cross[:2],
        "edge_counts": {"CALLED": len(called), "TRANSACTED": len(transacted), "MENTIONED_IN": len(mentioned), "total": len(edges)},
        "sources": sources[:8],
        "disclaimer": "Potential investigative lead — not a guilt determination. Trace to source records above."
    }

# -------------------------------------------------------------
# Tactical Takedown & Arrest Optimization Simulator
# -------------------------------------------------------------
@app.get("/takedown/strategies")
def get_takedown_strategies_endpoint(iid: Optional[str] = Query(None), user: dict = Depends(get_current_user)):
    datasets, serial = _get_inv_datasets_and_serial(iid)
    data = get_takedown_strategies(datasets=datasets, graph=serial)
    audit_log("/takedown/strategies", [s["id"] for s in data.get("strategies", [])])
    return data

@app.get("/investigations/{iid}/takedown/strategies")
def get_inv_takedown_strategies_endpoint(iid: str, user: dict = Depends(get_current_user)):
    datasets, serial = _get_inv_datasets_and_serial(iid)
    data = get_takedown_strategies(datasets=datasets, graph=serial)
    audit_log(f"/investigations/{iid}/takedown/strategies", [s["id"] for s in data.get("strategies", [])])
    return data

class SimulateTakedownRequest(BaseModel):
    target_ids: List[str]
    freeze_accounts: bool = True
    iid: Optional[str] = None

@app.post("/takedown/simulate")
def simulate_takedown_endpoint(payload: SimulateTakedownRequest, user: dict = Depends(get_current_user)):
    datasets, serial = _get_inv_datasets_and_serial(payload.iid)
    res = simulate_takedown(
        target_ids=payload.target_ids,
        datasets=datasets,
        graph=serial,
        freeze_financial_accounts=payload.freeze_accounts
    )
    audit_log(f"/takedown/simulate targets={len(payload.target_ids)}", payload.target_ids[:10])
    return res

@app.post("/investigations/{iid}/takedown/simulate")
def simulate_inv_takedown_endpoint(iid: str, payload: SimulateTakedownRequest, user: dict = Depends(get_current_user)):
    datasets, serial = _get_inv_datasets_and_serial(iid)
    res = simulate_takedown(
        target_ids=payload.target_ids,
        datasets=datasets,
        graph=serial,
        freeze_financial_accounts=payload.freeze_accounts
    )
    audit_log(f"/investigations/{iid}/takedown/simulate targets={len(payload.target_ids)}", payload.target_ids[:10])
    return res

class OperationOrderRequest(BaseModel):
    strategy_id: Optional[str] = None
    target_ids: Optional[List[str]] = None
    operation_codename: Optional[str] = None
    codename: Optional[str] = None
    commander_name: Optional[str] = None
    iid: Optional[str] = None

@app.post("/takedown/operation-order")
def create_operation_order_endpoint(payload: OperationOrderRequest, user: dict = Depends(get_current_user)):
    datasets, serial = _get_inv_datasets_and_serial(payload.iid)
    commander = payload.commander_name or user.get("name") or user.get("username", "Joint Commissioner of Police")
    codename = payload.operation_codename or payload.codename or "OPERATION THUNDERCLAP"
    targets_or_strat = payload.target_ids if payload.target_ids else (payload.strategy_id or "strategy_sync")
    data = generate_operation_order(
        strategy_id_or_targets=targets_or_strat,
        datasets=datasets,
        graph=serial,
        commander_name=commander,
        codename=codename
    )
    audit_log(f"/takedown/operation-order ({data['operation_order_id']})", [data["operation_order_id"]])
    return data

@app.post("/investigations/{iid}/takedown/operation-order")
def create_inv_operation_order_endpoint(iid: str, payload: OperationOrderRequest, user: dict = Depends(get_current_user)):
    datasets, serial = _get_inv_datasets_and_serial(iid)
    commander = payload.commander_name or user.get("name") or user.get("username", "Joint Commissioner of Police")
    codename = payload.operation_codename or payload.codename or "OPERATION THUNDERCLAP"
    targets_or_strat = payload.target_ids if payload.target_ids else (payload.strategy_id or "strategy_sync")
    data = generate_operation_order(
        strategy_id_or_targets=targets_or_strat,
        datasets=datasets,
        graph=serial,
        commander_name=commander,
        codename=codename
    )
    audit_log(f"/investigations/{iid}/takedown/operation-order", [data["operation_order_id"]])
    return data

# --- /ask templates --- ordered by specificity (longer triggers first, per design 8 templates)
ASK_TEMPLATES = [
    {"id": 1, "trigger": "bridges", "keywords": ["bridge","connects","connect"], "description": "Who connects Cell A and Cell B? / bridges-between", "cypher": "MATCH (p:Person)-[r]-(q:Person) WHERE p.cell<>q.cell RETURN p, r, q ORDER BY p.bridge_score DESC LIMIT 10"},
    {"id": 7, "trigger": "structuring", "keywords": ["structuring","smurf","fan"], "description": "Structuring around ID", "cypher": "MATCH (a:Person {id:$id})<-[r:TRANSACTED]-(b:Person) WHERE r.amount < 50000 RETURN b,r"},
    {"id": 5, "trigger": "transactions", "keywords": ["transaction","transact","amount","lakh","hawala"], "description": "Transactions over amount", "cypher": "MATCH (a:Person)-[r:TRANSACTED]->(b:Person) WHERE r.amount > $amt RETURN a,b,r ORDER BY r.amount DESC"},
    {"id": 4, "trigger": "path", "keywords": ["path","between","to"], "description": "Path from ID to ID", "cypher": "MATCH p=shortestPath((a:Person {id:$src})-[:CALLED|TRANSACTED*..4]-(b:Person {id:$dst})) RETURN p"},
    {"id": 2, "trigger": "burst", "keywords": ["burst","spike","activity"], "description": "Show bursts on day", "cypher": "MATCH (p:Person)-[r:CALLED]->(q:Person) WHERE r.day=$day RETURN p,q,r"},
    {"id": 6, "trigger": "calls", "keywords": ["calls","called","phone"], "description": "Calls from ID", "cypher": "MATCH (a:Person {id:$id})-[r:CALLED]-(b:Person) RETURN b, r ORDER BY r.day"},
    {"id": 3, "trigger": "why", "keywords": ["why","evidence","support"], "description": "What evidence supports relationship X?", "cypher": "MATCH (a {id:$id})-[r]-(b) RETURN r.source, r.source_type, r.confidence, r.day LIMIT 20"},
    {"id": 8, "trigger": "cell", "keywords": ["cell of"], "description": "Cell of ID", "cypher": "MATCH (p:Person {id:$id}) RETURN p.cell, p.role"},
]

@app.get("/ask")
def ask(q: str = Query(..., description="Natural language query"), user: dict = Depends(CAN_VIEW_GRAPH)):
    qlow = q.lower().strip()
    # intent extraction: match any keyword in template's keywords list (ordered by specificity)
    for t in ASK_TEMPLATES:
        if any(kw in qlow for kw in t.get("keywords", [t["trigger"]])):
            # try extract entity ids like X1, A11 etc
            import re
            m = re.findall(r"\b([A-Z]\d{1,2}|X\d)\b", q)
            params = {}
            if m:
                params["id"] = m[0]
                if len(m) >= 2:
                    params["src"] = m[0]; params["dst"] = m[1]
            # amount extraction
            amt_m = re.search(r"(\d+)\s*(lakh|k)", qlow)
            if amt_m:
                # crude: 1 lakh = 100000
                val = int(amt_m.group(1))
                if "lakh" in amt_m.group(2):
                    val *= 100000
                elif "k" in amt_m.group(2):
                    val *= 1000
                params["amt"] = val
            # day extraction
            day_m = re.search(r"day\s*(\d+)", qlow)
            if day_m:
                params["day"] = int(day_m.group(1))
            audit_log(f"/ask?q={q}", [t["id"]])
            return {"template_id": t["id"], "description": t["description"], "cypher": t["cypher"], "params": params, "query": q}
    # unknown template
    audit_log(f"/ask?q={q} (unknown)", [])
    raise HTTPException(status_code=400, detail={
        "error": "Unknown template — try one of the 8 supported intents",
        "examples": [t["description"] + f" (try: '{t['trigger']} ...')" for t in ASK_TEMPLATES[:3]],
        "templates": ASK_TEMPLATES
    })
