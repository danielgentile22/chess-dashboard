"""
auth.py
=======
The login gate (issue #71 [G1]).

A lightweight session gate over the Flask/Dash server.  Because the dashboard
surfaces a coach's *private* third-party material, an unauthenticated request
must never reach a page — so the gate is installed as a Flask ``before_request``
hook that bounces everything except the login page and static assets to
``/login`` until a valid session exists.

Access is granted only by adding a record to the multi-user config (see
``user_config``); there is no self-service signup and no settings UI.  A valid
username + password sets a signed-cookie session that persists across page
navigation; a wrong password is refused.

The gate is installed only when users are configured.  With no users the
dashboard runs in its original single-user, ungated mode (local development and
the existing test suite are unchanged).

Public API
----------
install_auth   Install the gate + login/logout routes on a Flask server.
current_user   The authenticated username for the current request (or None).
Auth           The installed gate's handle (``enabled``, ``authenticate``).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import timedelta

from flask import (
    Flask,
    Response,
    redirect,
    request,
    session,
)
from markupsafe import escape
from werkzeug.security import check_password_hash, generate_password_hash

from user_config import UserRecord

# A precomputed hash the authenticate() path checks against on a username miss,
# so an unknown user costs the same scrypt work as a known one — no username
# enumeration by response time (issue #89 [F7]).
_DUMMY_HASH = generate_password_hash("*never-a-real-password*")

# Login throttle (issue #89 [F8]): after this many failures for one
# (IP, username) within the window, further attempts are refused until the
# window elapses.  In-memory only — state resets on restart, which is fine for
# a throttle and consistent with the no-database design.
_THROTTLE_MAX_FAILS = 5
_THROTTLE_WINDOW_S = 300.0
# ponytail: per-(ip, user) counter, capped in size; fine for a small allow-list.
# Upgrade path if the login surface grows: an IP-level bucket + redis/LRU so a
# single IP rotating usernames can't sidestep the per-key lockout.
_THROTTLE_MAX_KEYS = 4096
_login_fails: dict[tuple[str, str], list[float]] = {}


def _throttled(key: tuple[str, str]) -> bool:
    """True if *key* has too many recent failures — refuse the attempt."""
    now = time.monotonic()
    recent = [t for t in _login_fails.get(key, ()) if now - t < _THROTTLE_WINDOW_S]
    _login_fails[key] = recent
    return len(recent) >= _THROTTLE_MAX_FAILS


def _record_failure(key: tuple[str, str]) -> None:
    now = time.monotonic()
    if len(_login_fails) >= _THROTTLE_MAX_KEYS:
        # Drop keys whose failures have all aged out — bounds memory against an
        # attacker rotating usernames to spawn endless buckets (#89).
        for stale in [k for k, ts in _login_fails.items()
                      if all(now - t >= _THROTTLE_WINDOW_S for t in ts)]:
            del _login_fails[stale]
    _login_fails.setdefault(key, []).append(now)

# Paths reachable without a session: the login/logout routes, static assets the
# login page needs, Dash's vendored component bundles (static JS, never data),
# and the health check.  Everything else — pages and Dash data callbacks — is
# gated.
_PUBLIC_PREFIXES = (
    "/login",
    "/logout",
    "/assets/",
    "/_dash-component-suites/",
    "/_reload-hash",
    "/_favicon.ico",
)
_PUBLIC_PATHS = ("/health", "/favicon.ico")

_SESSION_KEY = "user"


@dataclass
class Auth:
    """The installed gate: the allow-listed users and the credential check."""

    users: dict[str, UserRecord]

    @property
    def enabled(self) -> bool:
        """True when any user is configured — the gate is active only then."""
        return bool(self.users)

    def authenticate(self, username: str, password: str) -> UserRecord | None:
        """The record for *username* if *password* is correct, else None.

        An unknown username still pays the full scrypt cost (against a dummy
        hash) so presence/absence can't be told apart by response time (#89).
        """
        record = self.users.get(username)
        if record is None:
            check_password_hash(_DUMMY_HASH, password)
            return None
        return record if record.verify(password) else None


def current_user() -> str | None:
    """The authenticated username for the current request, or None.

    Reads the signed-cookie session, so it is meaningful only inside a request
    context; outside one (or before login) it is None.
    """
    try:
        return session.get(_SESSION_KEY)
    except RuntimeError:
        # No request/app context (e.g. a background thread) → nobody is logged in.
        return None


def install_auth(
    server: Flask,
    users: dict[str, UserRecord],
    *,
    secret_key: str,
    login_path: str = "/login",
    secure_cookies: bool = True,
) -> Auth:
    """
    Gate *server* behind a login, and register the login/logout routes.

    The session is signed with *secret_key*; set a stable, secret value in
    production (``SECRET_KEY``) so sessions survive restarts and cannot be
    forged.  *secure_cookies* marks the session cookie ``Secure`` (HTTPS-only);
    pass ``False`` only for local HTTP dev.  Returns the :class:`Auth` handle,
    also stored on ``server.extensions['uscf_auth']``.
    """
    server.secret_key = secret_key
    # Harden the session cookie (issue #89 [F3]): HTTPS-only in production, never
    # readable from JS, SameSite=Lax against cross-site POSTs, and a bounded
    # lifetime so a leaked cookie can't be replayed forever.
    server.config.update(
        SESSION_COOKIE_SECURE=secure_cookies,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    )
    gate = Auth(users)
    server.extensions["uscf_auth"] = gate

    @server.before_request
    def _require_login():
        path = request.path
        if path in _PUBLIC_PATHS or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return None
        if session.get(_SESSION_KEY) in users:
            return None
        return redirect(login_path)

    @server.route(login_path, methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = request.form.get("username", "")
            password = request.form.get("password", "")
            throttle_key = (request.remote_addr or "?", username)
            if _throttled(throttle_key):
                return Response(
                    _login_page(error="Too many attempts — wait a few minutes."),
                    status=429, mimetype="text/html")
            if gate.authenticate(username, password) is not None:
                _login_fails.pop(throttle_key, None)
                session.permanent = True  # apply PERMANENT_SESSION_LIFETIME
                session[_SESSION_KEY] = username
                target = _safe_next(request.form.get("next", ""))
                return redirect(target)
            _record_failure(throttle_key)
            return Response(_login_page(error="Wrong username or password."),
                            status=401, mimetype="text/html")
        if session.get(_SESSION_KEY) in users:
            return redirect("/")
        return Response(_login_page(next_path=_safe_next(request.args.get("next", ""))),
                        mimetype="text/html")

    # POST-only: a GET /logout is CSRF-able (`<img src=".../logout">` would log a
    # user out from any page), so state change requires the shell's logout form.
    @server.route("/logout", methods=["POST"])
    def logout():
        session.pop(_SESSION_KEY, None)
        return redirect(login_path)

    return gate


def _safe_next(raw: str) -> str:
    """A post-login redirect target, restricted to a local path (no open
    redirect to another host).

    Rejects both ``//host`` and the backslash form ``/\\host`` — browsers
    normalise ``\\`` to ``/`` per the WHATWG URL spec, so ``/\\evil.com``
    resolves off-site (issue #89 [F4]).
    """
    if raw.startswith("/") and not raw.startswith(("//", "/\\")):
        return raw
    return "/"


def _login_page(*, error: str = "", next_path: str = "/") -> str:
    """The standalone login page — self-contained so it needs no Dash assets."""
    error_html = (
        f'<p class="login-error">{escape(error)}</p>' if error else ""
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sign in — Chess Dashboard</title>
  <style>
    :root {{ color-scheme: dark; }}
    body {{ margin: 0; min-height: 100vh; display: grid; place-items: center;
            background: #0b0d12; color: #e7e9ee;
            font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    .login-card {{ width: 320px; padding: 32px; border-radius: 16px;
                   background: #151922; box-shadow: 0 12px 40px rgba(0,0,0,.45); }}
    .login-title {{ margin: 0 0 4px; font-size: 22px; font-weight: 650; }}
    .login-sub {{ margin: 0 0 24px; color: #9aa0ad; font-size: 14px; }}
    label {{ display: block; margin: 0 0 6px; font-size: 13px; color: #9aa0ad; }}
    input {{ width: 100%; box-sizing: border-box; margin: 0 0 16px; padding: 10px 12px;
             border: 1px solid #2a2f3a; border-radius: 10px; background: #0e1117;
             color: #e7e9ee; font-size: 15px; }}
    input:focus {{ outline: 2px solid #4c8bf5; border-color: transparent; }}
    button {{ width: 100%; padding: 11px; border: 0; border-radius: 10px; cursor: pointer;
              background: #4c8bf5; color: #fff; font-size: 15px; font-weight: 600; }}
    button:hover {{ background: #3f7ae0; }}
    .login-error {{ margin: 0 0 16px; padding: 9px 12px; border-radius: 8px;
                    background: rgba(229,72,77,.14); color: #ff8b8f; font-size: 13px; }}
  </style>
</head>
<body>
  <form class="login-card" method="post" action="/login">
    <h1 class="login-title">Chess Dashboard</h1>
    <p class="login-sub">Sign in to see your dashboard.</p>
    {error_html}
    <input type="hidden" name="next" value="{escape(next_path)}">
    <label for="username">Username</label>
    <input id="username" name="username" autocomplete="username" autofocus required>
    <label for="password">Password</label>
    <input id="password" name="password" type="password"
           autocomplete="current-password" required>
    <button type="submit">Sign in</button>
  </form>
</body>
</html>"""
