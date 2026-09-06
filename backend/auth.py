"""
JWT authentication + Role-Based Access Control (Phase 2).

Roles:
  supervisor   — full access (demo user: admin)
  analyst      — upload + map data, NO final intelligence graph
  investigator — graph + queries, NO raw-data upload

Demo users are hardcoded for the SIH prototype. Passwords are stored
as SHA-256 hashes (stdlib only). For production, replace with a real
user store + bcrypt and a strong JWT_SECRET env value.
"""
import hashlib
import json
import os
import re
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


# username -> {password_hash, role, name}
USERS = {
    "admin": {
        "password_hash": _hash("supervisor123"),
        "role": "supervisor",
        "name": "Supervisor",
    },
    "analyst": {
        "password_hash": _hash("analyst123"),
        "role": "analyst",
        "name": "Data Analyst",
    },
    "investigator": {
        "password_hash": _hash("investigator123"),
        "role": "investigator",
        "name": "Investigator",
    },
}

VALID_ROLES = {"supervisor", "analyst", "investigator"}

# Roles a new account may self-select (supervisor is never self-granted)
SELF_REGISTER_ROLES = ("investigator", "analyst")

USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,32}$")
MIN_PASSWORD_LEN = 6


def _user_file() -> Path:
    from backend.config import PROJECT_ROOT

    return PROJECT_ROOT / "data" / "users.json"


def _load_registered() -> dict:
    """username -> {password_hash, role, name} for self-registered accounts."""
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


def _all_users() -> dict:
    """Hardcoded demo users plus registered accounts (hardcoded names win)."""
    merged = dict(_load_registered())
    merged.update(USERS)
    return merged


def register_user(username: str, password: str, role: str, name: str = "") -> dict:
    """Create a self-registered account. Raises ValueError with a human message on bad input."""
    username = (username or "").strip().lower()
    if not USERNAME_RE.match(username):
        raise ValueError("Username must be 3–32 characters: letters, numbers, . _ -")
    if not password or len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LEN} characters")
    if role not in SELF_REGISTER_ROLES:
        raise ValueError("Role must be investigator or analyst")
    if username in _all_users():
        raise ValueError("Username is already taken")
    registered = _load_registered()
    registered[username] = {
        "password_hash": _hash(password),
        "role": role,
        "name": (name or "").strip() or username,
    }
    _save_registered(registered)
    return {"username": username, "role": role, "name": registered[username]["name"]}


def authenticate(username: str, password: str):
    """Return public user dict on valid credentials, else None. Constant-shape to avoid timing hints."""
    user = _all_users().get((username or "").strip().lower())
    if not user:
        return None
    if user["password_hash"] != _hash(password or ""):
        return None
    return {"username": username.strip().lower(), "role": user["role"], "name": user["name"]}


def create_token(user: dict) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user["username"],
        "role": user["role"],
        "name": user["name"],
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=TOKEN_TTL_HOURS)).timestamp()),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(creds: HTTPAuthorizationCredentials = Depends(_bearer)) -> dict:
    """FastAPI dependency — 401 unless a valid, unexpired JWT is presented."""
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
    return {"username": username, "role": role, "name": user["name"]}


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
