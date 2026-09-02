"""Passkey (WebAuthn) gate for the public dashboard.

Loopback requests without proxy headers (no cf-ray / x-forwarded-for) are the
owner's own machine and skip auth; public requests via the cloudflared tunnel
need a session cookie minted by a passkey ceremony. The first passkey enrolls
only from a trusted context (local browser, an existing session, or
EMOTIV_SETUP_CODE). The store is one JSON file (~/.emotiv/auth.json, 0600),
hot-reloaded on mtime.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

COOKIE = "emotiv_session"
SESSION_TTL = int(os.getenv("EMOTIV_SESSION_TTL", str(30 * 86400)))
RP_NAME = "strands-emotiv · the mirror"
# hosts we will mint credentials for (host-header allowlist)
_DEFAULT_HOSTS = "brain.cagatay.my,localhost:8765,127.0.0.1:8765,localhost:5173"
ALLOWED_HOSTS = {h.strip() for h in
                 os.getenv("EMOTIV_AUTH_HOSTS", _DEFAULT_HOSTS).split(",") if h.strip()}

_PROXY_HEADERS = ("cf-ray", "cf-connecting-ip", "x-forwarded-for")


def store_path() -> Path:
    return Path(os.getenv("EMOTIV_AUTH_STORE",
                          str(Path.home() / ".emotiv" / "auth.json"))).expanduser()


# ---------- store: one JSON file, cached on (path, mtime) ----------

_cache: dict[tuple, dict[str, Any]] = {}


def _load() -> dict[str, Any]:
    p = store_path()
    try:
        key = (str(p), p.stat().st_mtime_ns)
    except FileNotFoundError:
        return {"credentials": [], "sessions": {}}
    if key not in _cache:
        _cache.clear()
        _cache[key] = json.loads(p.read_text())
    return _cache[key]


def _save(store: dict[str, Any]) -> None:
    p = store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    store["sessions"] = {k: v for k, v in store.get("sessions", {}).items()
                         if v.get("exp", 0) > now}
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, indent=2))
    tmp.chmod(0o600)
    tmp.replace(p)
    _cache.clear()


def has_credentials() -> bool:
    return bool(_load().get("credentials"))


def auth_enabled() -> bool:
    """The store is the source of truth: a passkey enrolled = the door locked.
    EMOTIV_AUTH=0/1 overrides for dev; unrecognized values are ignored."""
    raw = os.getenv("EMOTIV_AUTH", "").strip().lower()
    if raw in ("1", "true", "on", "yes"):
        return True
    if raw in ("0", "false", "off", "no"):
        return False
    return has_credentials()


# ---------- request classification ----------

def is_local(request: Request) -> bool:
    """Loopback peer AND no proxy fingerprints = the owner's own machine."""
    host = getattr(request.client, "host", None)
    if host not in ("127.0.0.1", "::1", "localhost"):
        return False
    return not any(h in request.headers for h in _PROXY_HEADERS)


def _session_ok(request: Request) -> bool:
    return session_token_ok(request.cookies.get(COOKIE, ""))


def session_token_ok(tok: str) -> bool:
    """Shared with the WS gate: a raw cookie value → is it a live session?"""
    if not tok:
        return False
    h = hashlib.sha256(tok.encode()).hexdigest()
    sess = _load().get("sessions", {}).get(h)
    return bool(sess and sess.get("exp", 0) > time.time())


def is_authed(request: Request) -> bool:
    return is_local(request) or _session_ok(request)


def guard(request: Request) -> JSONResponse | None:
    """None = pass. Mounted as middleware for /api/* and consulted by /ws."""
    if not auth_enabled() or is_authed(request):
        return None
    return JSONResponse({"ok": False, "error": "passkey required",
                         "login": "/api/auth/login/begin"}, status_code=401)


# ---------- rp id / origin from the request (allowlisted) ----------

def _rp(request: Request) -> tuple[str, str]:
    host = request.headers.get("host", "")
    if ALLOWED_HOSTS and host not in ALLOWED_HOSTS:
        raise ValueError(f"host {host!r} is not in EMOTIV_AUTH_HOSTS")
    proto = request.headers.get("x-forwarded-proto") or \
        ("https" if "cf-ray" in request.headers else request.url.scheme)
    return host.split(":")[0], f"{proto}://{host}"


# ---------- ceremonies (single user, one pending challenge per kind) ----------

_pending: dict[str, tuple[bytes, float]] = {}


def _challenge(kind: str) -> bytes | None:
    ch, exp = _pending.pop(kind, (None, 0))
    return ch if ch and exp > time.time() else None


def _may_register(request: Request, body: dict) -> bool:
    if is_authed(request):
        return True
    code = os.getenv("EMOTIV_SETUP_CODE", "").strip()
    return bool(code) and secrets.compare_digest(body.get("setup_code", ""), code)


def _set_cookie(resp: JSONResponse, request: Request) -> None:
    tok = secrets.token_urlsafe(32)
    store = _load()
    store.setdefault("sessions", {})[hashlib.sha256(tok.encode()).hexdigest()] = {
        "exp": time.time() + SESSION_TTL, "created": time.time()}
    _save(store)
    secure = request.headers.get("x-forwarded-proto") == "https" or \
        "cf-ray" in request.headers
    resp.set_cookie(COOKIE, tok, max_age=SESSION_TTL, httponly=True,
                    samesite="lax", secure=secure)


router = APIRouter(prefix="/api/auth")


@router.get("/status")
async def status(request: Request):
    return {"enabled": auth_enabled(), "authed": is_authed(request),
            "local": is_local(request),
            "registered": len(_load().get("credentials", []))}


@router.post("/register/begin")
async def register_begin(request: Request):
    body = await request.json() if int(request.headers.get("content-length") or 0) else {}
    if not _may_register(request, body):
        return JSONResponse({"ok": False, "error":
                             "enroll from the Mac itself, an authed session, "
                             "or with the setup code"}, status_code=403)
    try:
        rp_id, _ = _rp(request)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    opts = generate_registration_options(
        rp_id=rp_id, rp_name=RP_NAME, user_id=b"cagatay", user_name="cagatay",
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(c["id"]))
            for c in _load().get("credentials", [])])
    _pending["reg"] = (opts.challenge, time.time() + 300)
    return JSONResponse(json.loads(options_to_json(opts)))


@router.post("/register/finish")
async def register_finish(request: Request):
    body = await request.json()
    ch = _challenge("reg")
    if not ch:
        return JSONResponse({"ok": False, "error": "no pending registration"},
                            status_code=400)
    try:
        rp_id, origin = _rp(request)
        v = verify_registration_response(
            credential=body.get("credential", body), expected_challenge=ch,
            expected_origin=origin, expected_rp_id=rp_id)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    store = _load()
    store.setdefault("credentials", []).append({
        "id": bytes_to_base64url(v.credential_id),
        "public_key": bytes_to_base64url(v.credential_public_key),
        "sign_count": v.sign_count,
        "name": body.get("name", "passkey"), "created": time.time()})
    resp = JSONResponse({"ok": True, "registered": len(store["credentials"])})
    _save(store)
    _set_cookie(resp, request)
    return resp


@router.post("/login/begin")
async def login_begin(request: Request):
    creds = _load().get("credentials", [])
    if not creds:
        return JSONResponse({"ok": False, "error": "no passkey enrolled yet"},
                            status_code=400)
    try:
        rp_id, _ = _rp(request)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    opts = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=[PublicKeyCredentialDescriptor(
            id=base64url_to_bytes(c["id"])) for c in creds],
        user_verification=UserVerificationRequirement.PREFERRED)
    _pending["auth"] = (opts.challenge, time.time() + 300)
    return JSONResponse(json.loads(options_to_json(opts)))


@router.post("/login/finish")
async def login_finish(request: Request):
    body = await request.json()
    ch = _challenge("auth")
    if not ch:
        return JSONResponse({"ok": False, "error": "no pending login"},
                            status_code=400)
    cred = body.get("credential", body)
    cred_id = cred.get("id", "")
    store = _load()
    match = next((c for c in store.get("credentials", [])
                  if c["id"] == cred_id), None)
    if not match:
        return JSONResponse({"ok": False, "error": "unknown credential"},
                            status_code=400)
    try:
        rp_id, origin = _rp(request)
        v = verify_authentication_response(
            credential=cred, expected_challenge=ch, expected_origin=origin,
            expected_rp_id=rp_id,
            credential_public_key=base64url_to_bytes(match["public_key"]),
            credential_current_sign_count=match["sign_count"])
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    match["sign_count"] = v.new_sign_count
    resp = JSONResponse({"ok": True})
    _save(store)
    _set_cookie(resp, request)
    return resp


@router.post("/logout")
async def logout(request: Request):
    tok = request.cookies.get(COOKIE, "")
    if tok:
        store = _load()
        store.get("sessions", {}).pop(
            hashlib.sha256(tok.encode()).hexdigest(), None)
        _save(store)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE)
    return resp
