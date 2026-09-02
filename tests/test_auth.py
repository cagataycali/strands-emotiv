"""The passkey door: store truth, local trust, session lifecycle, the gate.

The lock protects a live EEG stream, so the tests pin the REFUSALS as hard
as the happy paths: remote-without-session is 401, spoofed Host is refused,
first-enroll from remote without the setup code is 403.
"""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from strands_emotiv import auth


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("EMOTIV_AUTH_STORE", str(tmp_path / "auth.json"))
    monkeypatch.delenv("EMOTIV_AUTH", raising=False)
    monkeypatch.delenv("EMOTIV_SETUP_CODE", raising=False)
    auth._cache.clear()
    auth._pending.clear()
    return tmp_path / "auth.json"


def _request(client_host="203.0.113.9", headers=None, cookies=""):
    hdrs = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    if cookies:
        hdrs.append((b"cookie", cookies.encode()))
    scope = {"type": "http", "method": "GET", "path": "/api/state",
             "headers": hdrs, "client": (client_host, 1234),
             "query_string": b"", "scheme": "http"}
    return Request(scope)


# ---------- enablement: the store is the source of truth ----------

def test_disabled_until_a_passkey_exists():
    assert auth.auth_enabled() is False
    assert auth.guard(_request()) is None  # open door while unenrolled


def test_enabled_the_moment_a_credential_lands(store):
    s = auth._load()
    s.setdefault("credentials", []).append({"id": "x", "public_key": "y", "sign_count": 0})
    auth._save(s)
    assert auth.auth_enabled() is True


def test_env_override_wins_when_spelled_right(monkeypatch):
    monkeypatch.setenv("EMOTIV_AUTH", "1")
    assert auth.auth_enabled() is True
    monkeypatch.setenv("EMOTIV_AUTH", "banana")  # misspelling ≠ auth off
    assert auth.auth_enabled() is False  # store empty → still store truth


# ---------- local trust ----------

def test_loopback_without_proxy_headers_is_local():
    assert auth.is_local(_request("127.0.0.1")) is True


def test_loopback_behind_the_tunnel_is_not_local():
    # cloudflared connects from loopback but stamps cf-ray
    assert auth.is_local(_request("127.0.0.1", {"cf-ray": "abc"})) is False
    assert auth.is_local(_request("127.0.0.1", {"x-forwarded-for": "1.2.3.4"})) is False


def test_remote_is_never_local():
    assert auth.is_local(_request("203.0.113.9")) is False


# ---------- the gate ----------

def test_remote_without_session_is_401(monkeypatch):
    monkeypatch.setenv("EMOTIV_AUTH", "1")
    denied = auth.guard(_request())
    assert denied is not None and denied.status_code == 401


def test_local_bypasses_even_when_enabled(monkeypatch):
    monkeypatch.setenv("EMOTIV_AUTH", "1")
    assert auth.guard(_request("127.0.0.1")) is None


def test_session_cookie_unlocks(monkeypatch):
    monkeypatch.setenv("EMOTIV_AUTH", "1")
    resp = JSONResponse({})
    auth._set_cookie(resp, _request())
    tok = resp.headers["set-cookie"].split("emotiv_session=")[1].split(";")[0]
    assert auth.session_token_ok(tok) is True
    assert auth.guard(_request(cookies=f"emotiv_session={tok}")) is None


def test_expired_sessions_are_pruned_on_save():
    s = auth._load()
    s.setdefault("sessions", {})["dead"] = {"exp": time.time() - 1}
    auth._save(s)
    assert "dead" not in auth._load()["sessions"]


def test_garbage_token_is_refused():
    assert auth.session_token_ok("not-a-session") is False
    assert auth.session_token_ok("") is False


# ---------- ceremonies over HTTP ----------

@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(auth.router)
    return TestClient(app)  # client host = "testclient" → remote


def test_status_shape(client):
    body = client.get("/api/auth/status").json()
    assert body == {"enabled": False, "authed": False, "local": False, "registered": 0}


def test_first_enroll_from_remote_needs_the_setup_code(client, monkeypatch):
    r = client.post("/api/auth/register/begin", json={})
    assert r.status_code == 403
    monkeypatch.setenv("EMOTIV_SETUP_CODE", "sesame")
    r = client.post("/api/auth/register/begin", json={"setup_code": "wrong"})
    assert r.status_code == 403
    r = client.post("/api/auth/register/begin",
                    json={"setup_code": "sesame"},
                    headers={"host": "brain.cagatay.my"})
    assert r.status_code == 200
    assert r.json()["rp"]["id"] == "brain.cagatay.my"


def test_spoofed_host_is_refused(client, monkeypatch):
    monkeypatch.setenv("EMOTIV_SETUP_CODE", "sesame")
    r = client.post("/api/auth/register/begin",
                    json={"setup_code": "sesame"},
                    headers={"host": "evil.example.com"})
    assert r.status_code == 400
    assert "not in EMOTIV_AUTH_HOSTS" in r.json()["error"]


def test_login_begin_refuses_with_no_passkeys(client):
    r = client.post("/api/auth/login/begin", headers={"host": "brain.cagatay.my"})
    assert r.status_code == 400


def test_finish_without_pending_challenge_is_400(client):
    r = client.post("/api/auth/register/finish", json={"credential": {}},
                    headers={"host": "brain.cagatay.my"})
    assert r.status_code == 400
    r = client.post("/api/auth/login/finish", json={"credential": {"id": "x"}},
                    headers={"host": "brain.cagatay.my"})
    assert r.status_code == 400


def test_challenge_is_single_use(client, monkeypatch):
    monkeypatch.setenv("EMOTIV_SETUP_CODE", "sesame")
    client.post("/api/auth/register/begin", json={"setup_code": "sesame"},
                headers={"host": "brain.cagatay.my"})
    assert auth._challenge("reg") is not None  # consumed
    assert auth._challenge("reg") is None      # gone


def test_logout_revokes(client, monkeypatch):
    resp = JSONResponse({})
    auth._set_cookie(resp, _request())
    tok = resp.headers["set-cookie"].split("emotiv_session=")[1].split(";")[0]
    assert auth.session_token_ok(tok)
    client.post("/api/auth/logout", cookies={"emotiv_session": tok})
    assert auth.session_token_ok(tok) is False
