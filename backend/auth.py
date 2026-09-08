"""
JWT authentication + Role-Based Access Control with
Departmental & Administrative Approval Lifecycle.

Roles:
  supervisor   — full access + admin clearance portal (demo user: admin)
  analyst      — upload + map data, NO final intelligence graph
  investigator — graph + queries, NO raw-data upload

Lifecycle (Government & Law-Enforcement Grade):
  open self-registration is REMOVED. New officers submit an Access Request
  (status ``pending_approval``) via ``request_access_user()``. A supervisor
  approves / rejects / suspends / provisions / resets via the admin helpers.
  Only ``status == "active"`` accounts can authenticate or use saved JWTs.

User record schema (data/users.json):
  {
    "password_hash": "...", "role": "investigator", "name": "...",
    "badge_id": "DL-CYBER-8842", "department": "Cyber Crime Investigation Cell",
    "justification": "Case work ...", "status": "active | pending_approval | suspended | rejected",
    "created_at": "2026-09-07T01:00:00Z",
    "approved_by": "admin", "approved_at": "2026-09-07T01:05:00Z",
    "rejection_reason": null
  }

Legacy records (only password_hash/role/name) are treated as active and
are backfilled with defaults on read — fully backward compatible.

Passwords are SHA-256 (stdlib only) for the SIH prototype. Production must
use bcrypt/scrypt + a strong JWT_SECRET.
"""
import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

SECRET_KEY = os.getenv("JWT_SECRET", "dev-only-secret-change-me-in-production-0123456789")
ALGORITHM = "HS256"
TOKEN_TTL_HOURS = 8

_bearer = HTTPBearer(auto_error=False)


def _hash(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# username -> full user record (seed accounts are pre-activated)
USERS = {
    "admin": {
        "password_hash": _hash("supervisor123"),
        "role": "supervisor",
        "name": "System Administrator",
        "badge_id": "ADMIN-001",
        "department": "Central Cyber & Intelligence Bureau",
        "justification": "",
        "status": "active",
        "created_at": "2026-01-01T00:00:00Z",
        "approved_by": "system",
        "approved_at": "2026-01-01T00:00:00Z",
        "rejection_reason": None,
    },
    "analyst": {
        "password_hash": _hash("analyst123"),
        "role": "analyst",
        "name": "Senior Intelligence Analyst",
        "badge_id": "ANL-001",
        "department": "Data Analytics & Intelligence Unit",
        "justification": "",
        "status": "active",
        "created_at": "2026-01-01T00:00:00Z",
        "approved_by": "system",
        "approved_at": "2026-01-01T00:00:00Z",
        "rejection_reason": None,
    },
    "investigator": {
        "password_hash": _hash("investigator123"),
        "role": "investigator",
        "name": "Senior Detective",
        "badge_id": "INV-001",
        "department": "Special Crime Branch",
        "justification": "",
        "status": "active",
        "created_at": "2026-01-01T00:00:00Z",
        "approved_by": "system",
        "approved_at": "2026-01-01T00:00:00Z",
        "rejection_reason": None,
    },
}

VALID_ROLES = {"supervisor", "analyst", "investigator"}

VALID_STATUSES = {"active", "pending_approval", "suspended", "rejected"}

# Roles a new access request may ask for (supervisor is never self-granted;
# only an existing supervisor can provision one via admin_create_user).
SELF_REGISTER_ROLES = ("investigator", "analyst")
REQUESTABLE_ROLES = ("investigator", "analyst")

USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,32}$")
MIN_PASSWORD_LEN = 6


# ---------------------------------------------------------------------------
# Status-gate errors — raised by authenticate() when credentials are correct
# but the account lifecycle state forbids sign-in. The API layer maps these
# to HTTP 403 with the user-facing message.
# ---------------------------------------------------------------------------
class AccountStatusError(ValueError):
    """Base for lifecycle blocks. Carries the account status + detail."""

    status_name = "blocked"

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class AccountPendingError(AccountStatusError):
    status_name = "pending_approval"


class AccountSuspendedError(AccountStatusError):
    status_name = "suspended"


class AccountRejectedError(AccountStatusError):
    status_name = "rejected"


def _user_file() -> Path:
    from backend.config import PROJECT_ROOT

    return PROJECT_ROOT / "data" / "users.json"


def _load_registered() -> dict:
    """username -> raw record for provisioned / requested accounts."""
    try:
        path = _user_file()
        if not path.exists():
            return {}
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_registered(users: dict) -> None:
    path = _user_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(users, indent=2))


def _normalize(username: str, rec: dict) -> dict:
    """Return a full-schema copy of a record (legacy records default to active)."""
    rec = dict(rec or {})
    rec.setdefault("role", "investigator")
    rec.setdefault("name", username)
    rec.setdefault("badge_id", "")
    rec.setdefault("department", "")
    rec.setdefault("justification", "")
    rec.setdefault("status", "active")
    rec.setdefault("created_at", None)
    rec.setdefault("approved_by", None)
    rec.setdefault("approved_at", None)
    rec.setdefault("rejection_reason", None)
    return rec


def _all_users() -> dict:
    """Hardcoded seed users plus file-backed accounts (seed names win)."""
    merged = {k: _normalize(k, v) for k, v in _load_registered().items()}
    for k, v in USERS.items():
        merged[k] = _normalize(k, v)
    return merged


def public_user(username: str, rec: dict) -> dict:
    """Full profile safe for API responses (never includes password_hash)."""
    rec = _normalize(username, rec)
    return {
        "username": username,
        "role": rec["role"],
        "name": rec["name"],
        "badge_id": rec.get("badge_id") or "",
        "department": rec.get("department") or "",
        "status": rec.get("status") or "active",
        "created_at": rec.get("created_at"),
        "approved_by": rec.get("approved_by"),
        "approved_at": rec.get("approved_at"),
        "rejection_reason": rec.get("rejection_reason"),
    }


def _validate_new_credentials(username: str, password: str, role: str, *, allow_supervisor: bool = False) -> str:
    username = (username or "").strip().lower()
    if not USERNAME_RE.match(username):
        raise ValueError("Username must be 3–32 characters: letters, numbers, . _ -")
    if not password or len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LEN} characters")
    allowed = VALID_ROLES if allow_supervisor else set(REQUESTABLE_ROLES)
    if (role or "").strip().lower() not in allowed:
        if allow_supervisor:
            raise ValueError("Role must be supervisor, investigator or analyst")
        raise ValueError("Role must be investigator or analyst")
    if username in _all_users():
        raise ValueError("Username is already taken")
    return username


def request_access_user(
    username: str,
    password: str,
    role: str,
    name: str = "",
    badge_id: str = "",
    department: str = "",
    justification: str = "",
) -> dict:
    """Submit a departmental access request (status=pending_approval).

    Raises ValueError with a human message on bad input. Returns the public
    profile — notably WITHOUT any session token.
    """
    username = _validate_new_credentials(username, password, role)
    role = (role or "").strip().lower()
    registered = _load_registered()
    if username in {k.lower() for k in registered} or username in USERS:
        raise ValueError("Username is already taken")
    registered[username] = {
        "password_hash": _hash(password),
        "role": role,
        "name": (name or "").strip() or username,
        "badge_id": (badge_id or "").strip(),
        "department": (department or "").strip(),
        "justification": (justification or "").strip(),
        "status": "pending_approval",
        "created_at": _now_iso(),
        "approved_by": None,
        "approved_at": None,
        "rejection_reason": None,
    }
    _save_registered(registered)
    return public_user(username, registered[username])


def register_user(username: str, password: str, role: str, name: str = "") -> dict:
    """Legacy entry point — now creates a PENDING access request.

    Open self-registration with instant tokens is removed; this wrapper keeps
    the old import path working while enforcing the approval lifecycle.
    """
    return request_access_user(username, password, role, name=name)


def admin_create_user(
    username: str,
    password: str,
    role: str,
    name: str = "",
    badge_id: str = "",
    department: str = "",
    created_by: str = "admin",
) -> dict:
    """Direct account provisioning by a supervisor — immediately active."""
    username = _validate_new_credentials(username, password, role, allow_supervisor=True)
    role = (role or "").strip().lower()
    registered = _load_registered()
    now = _now_iso()
    registered[username] = {
        "password_hash": _hash(password),
        "role": role,
        "name": (name or "").strip() or username,
        "badge_id": (badge_id or "").strip(),
        "department": (department or "").strip(),
        "justification": "",
        "status": "active",
        "created_at": now,
        "approved_by": created_by,
        "approved_at": now,
        "rejection_reason": None,
    }
    _save_registered(registered)
    return public_user(username, registered[username])


def _require_file_user(username: str) -> tuple:
    username = (username or "").strip().lower()
    if username in USERS:
        raise ValueError("Seed accounts cannot be modified via the admin API")
    registered = _load_registered()
    if username not in registered:
        raise KeyError(f"Unknown user '{username}'")
    return username, registered


def approve_user(username: str, approved_by: str, role: str = None) -> dict:
    """Approve a pending (or rejected) application → active."""
    username, registered = _require_file_user(username)
    rec = _normalize(username, registered[username])
    if rec["status"] not in ("pending_approval", "rejected", "suspended"):
        raise ValueError(f"User '{username}' is already {rec['status']}")
    if role is not None:
        role = (role or "").strip().lower()
        if role not in VALID_ROLES:
            raise ValueError("Role must be supervisor, investigator or analyst")
        rec["role"] = role
    rec["status"] = "active"
    rec["approved_by"] = approved_by
    rec["approved_at"] = _now_iso()
    rec["rejection_reason"] = None
    registered[username] = rec
    _save_registered(registered)
    return public_user(username, rec)


def reject_user(username: str, rejected_by: str, reason: str = "") -> dict:
    """Reject a pending application with an administrative reason."""
    username, registered = _require_file_user(username)
    rec = _normalize(username, registered[username])
    if rec["status"] not in ("pending_approval", "suspended"):
        raise ValueError(f"Only pending or suspended accounts can be rejected (now {rec['status']})")
    reason = (reason or "").strip() or "Not approved by department"
    rec["status"] = "rejected"
    rec["rejection_reason"] = reason
    rec["approved_by"] = rejected_by
    rec["approved_at"] = _now_iso()
    registered[username] = rec
    _save_registered(registered)
    return public_user(username, rec)


def update_user_status(username: str, new_status: str, actor: str = "admin") -> dict:
    """Suspend / reactivate an account (active ↔ suspended)."""
    new_status = (new_status or "").strip().lower()
    if new_status not in ("active", "suspended"):
        raise ValueError("Status must be active or suspended")
    username, registered = _require_file_user(username)
    rec = _normalize(username, registered[username])
    if rec["status"] == new_status:
        return public_user(username, rec)
    if rec["status"] not in ("active", "suspended"):
        raise ValueError(f"Cannot change status from {rec['status']} — approve or reject first")
    rec["status"] = new_status
    rec["approved_by"] = actor
    rec["approved_at"] = _now_iso()
    registered[username] = rec
    _save_registered(registered)
    return public_user(username, rec)


def admin_reset_password(username: str, new_password: str = None) -> dict:
    """Set a temporary password for an officer. Generates one if omitted."""
    username, registered = _require_file_user(username)
    if new_password is not None and len(new_password) < MIN_PASSWORD_LEN:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LEN} characters")
    temp = new_password or f"TEMP-{secrets.token_hex(4).upper()}"
    rec = _normalize(username, registered[username])
    rec["password_hash"] = _hash(temp)
    registered[username] = rec
    _save_registered(registered)
    profile = public_user(username, rec)
    profile["temporary_password"] = temp
    return profile


def list_all_users(status_filter: str = None, role_filter: str = None, search: str = None) -> list:
    """All accounts (seed + file) as public profiles, newest file users first."""
    users = _all_users()
    out = []
    for uname, rec in users.items():
        pub = public_user(uname, rec)
        if status_filter and pub["status"] != status_filter.strip().lower():
            continue
        if role_filter and pub["role"] != role_filter.strip().lower():
            continue
        if search:
            hay = f"{pub['username']} {pub['name']} {pub['badge_id']} {pub['department']}".lower()
            if search.strip().lower() not in hay:
                continue
        out.append(pub)
    # Pending first, then by created_at desc; seeds (created 2026-01-01) sink naturally
    rank = {"pending_approval": 0, "suspended": 1, "rejected": 2, "active": 3}
    out.sort(key=lambda u: (rank.get(u["status"], 9), u.get("created_at") or ""))
    return out


def get_user_profile(username: str) -> dict | None:
    user = _all_users().get((username or "").strip().lower())
    if not user:
        return None
    return public_user((username or "").strip().lower(), user)


def _status_error_for(rec: dict) -> AccountStatusError:
    st = (rec.get("status") or "active").lower()
    if st == "pending_approval":
        return AccountPendingError("Account pending administrative authorization. Contact your Department Administrator.")
    if st == "suspended":
        return AccountSuspendedError("Account has been suspended. Contact System Administrator.")
    if st == "rejected":
        reason = rec.get("rejection_reason") or "not approved"
        return AccountRejectedError(f"Access request was rejected: {reason}")
    return AccountStatusError(f"Account is {st}. Contact System Administrator.")


def authenticate(username: str, password: str):
    """Return public session dict on valid ACTIVE credentials.

    Returns None for unknown user / bad password. Raises AccountStatusError
    (pending / suspended / rejected) when credentials are correct but the
    lifecycle state forbids sign-in — the API maps these to HTTP 403.
    """
    uname = (username or "").strip().lower()
    user = _all_users().get(uname)
    if not user:
        return None
    if user["password_hash"] != _hash(password or ""):
        return None
    if (user.get("status") or "active") != "active":
        raise _status_error_for(user)
    return public_user(uname, user)


def create_token(user: dict) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user["username"],
        "role": user["role"],
        "name": user.get("name") or user["username"],
        "department": user.get("department") or "",
        "badge_id": user.get("badge_id") or "",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=TOKEN_TTL_HOURS)).timestamp()),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(creds: HTTPAuthorizationCredentials = Depends(_bearer)) -> dict:
    """FastAPI dependency — 401 unless a valid, unexpired JWT for an ACTIVE account."""
    if creds is None or not creds.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        payload = jwt.decode(creds.credentials, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired — please log in again")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session — please log in again")
    username, role = payload.get("sub"), payload.get("role")
    user = _all_users().get(username)
    if not user or user["role"] != role or role not in VALID_ROLES:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session — please log in again")
    if (user.get("status") or "active") != "active":
        # Token outlived the account (suspended after login) — force re-auth
        # with the lifecycle reason so the UI can explain it.
        err = _status_error_for(user)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=err.detail)
    return public_user(username, user)


def require_roles(*allowed: str):
    """Dependency factory — 403 unless the caller's role is in `allowed`."""
    allowed_set = set(allowed)

    def check(user: dict = Depends(get_current_user)) -> dict:
        if user["role"] not in allowed_set:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user['role']}' is not permitted for this action",
            )
        return user

    return check


# Convenience role groups used by the API routes
CAN_UPLOAD = require_roles("analyst", "supervisor", "investigator")
CAN_VIEW_GRAPH = require_roles("investigator", "supervisor")
REQUIRE_SUPERVISOR = require_roles("supervisor")
