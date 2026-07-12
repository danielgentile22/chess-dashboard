"""
tests/test_auth.py
==================
The login gate (issue #71 [G1]).

An unauthenticated request never reaches a page; a valid login is accepted and
resolves to the right user; a wrong password is refused; the session persists
across navigation.  Tested through the real Flask server with its test client,
the way a browser exercises it.
"""
from __future__ import annotations

from unittest import mock

import flask
import pytest

import auth
from user_config import UserRecord, hash_password


def _users() -> dict[str, UserRecord]:
    def rec(name, pw):
        return UserRecord(username=name, password_hash=hash_password(pw),
                          study_ids=("study-" + name,), coach_study_ids=(),
                          uscf_member_id=None, lichess_token=None)
    return {"daniel": rec("daniel", "hunter2"), "friend": rec("friend", "swordfish")}


@pytest.fixture()
def app():
    """A minimal Flask server with the gate installed and one protected route."""
    server = flask.Flask(__name__)
    # secure_cookies=False so the HTTP test client keeps the session cookie
    # (production sets it via secure_cookies=not DEBUG).
    auth.install_auth(server, _users(), secret_key="test-secret", secure_cookies=False)

    @server.route("/")
    def home():
        return f"hello {auth.current_user()}"

    @server.route("/health")
    def health():
        return "ok"

    return server


@pytest.fixture()
def client(app):
    return app.test_client()


def _login(client, username, password):
    return client.post("/login", data={"username": username, "password": password})


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

class TestGate:
    def test_unauthenticated_request_is_redirected_to_login(self, client):
        resp = client.get("/")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_login_page_is_reachable_without_auth(self, client):
        resp = client.get("/login")
        assert resp.status_code == 200
        assert b"password" in resp.data.lower()

    def test_health_check_is_not_gated(self, client):
        assert client.get("/health").status_code == 200

    def test_assets_are_not_gated(self, app):
        # Static assets the login page needs must be reachable pre-auth.
        client = app.test_client()
        # /assets/ is whitelisted even though this minimal app serves nothing there
        resp = client.get("/assets/whatever.css")
        assert resp.status_code != 302  # not bounced to login (404 is fine)


# ---------------------------------------------------------------------------
# Logging in
# ---------------------------------------------------------------------------

class TestLogin:
    def test_valid_login_is_accepted_and_reaches_the_page(self, client):
        resp = _login(client, "daniel", "hunter2")
        assert resp.status_code == 302  # redirected into the app
        page = client.get("/")
        assert page.status_code == 200
        assert b"hello daniel" in page.data

    def test_login_resolves_to_the_right_user(self, client):
        _login(client, "friend", "swordfish")
        assert b"hello friend" in client.get("/").data

    def test_wrong_password_is_refused(self, client):
        resp = _login(client, "daniel", "nope")
        # not redirected into the app, and the page stays gated
        assert b"hello daniel" not in resp.data
        assert client.get("/").status_code == 302

    def test_unknown_user_is_refused(self, client):
        _login(client, "stranger", "whatever")
        assert client.get("/").status_code == 302

    def test_session_persists_across_navigation(self, client):
        _login(client, "daniel", "hunter2")
        # Several page loads on the same client (one browser session)
        assert client.get("/").status_code == 200
        assert client.get("/").status_code == 200

    def test_repeated_failures_are_throttled(self, client):
        auth._login_fails.clear()
        for _ in range(auth._THROTTLE_MAX_FAILS):
            assert _login(client, "daniel", "wrong").status_code == 401
        # Next attempt is refused up-front (429), even with the right password
        assert _login(client, "daniel", "hunter2").status_code == 429
        auth._login_fails.clear()


# ---------------------------------------------------------------------------
# Logging out
# ---------------------------------------------------------------------------

class TestLogout:
    def test_logout_clears_the_session(self, client):
        _login(client, "daniel", "hunter2")
        assert client.get("/").status_code == 200
        client.post("/logout")  # POST-only: GET /logout is CSRF-able (#89)
        assert client.get("/").status_code == 302

    def test_logout_get_is_rejected(self, client):
        _login(client, "daniel", "hunter2")
        assert client.get("/logout").status_code == 405  # no CSRF-able GET


# ---------------------------------------------------------------------------
# Enablement
# ---------------------------------------------------------------------------

class TestEnablement:
    def test_auth_is_enabled_when_users_are_configured(self):
        a = auth.Auth(_users())
        assert a.enabled is True

    def test_auth_is_disabled_with_no_users(self):
        assert auth.Auth({}).enabled is False


# ---------------------------------------------------------------------------
# The gate, wired into the real Dash app via build_app (issue #71)
# ---------------------------------------------------------------------------

class TestBuildAppGate:
    def test_built_app_gates_pages_when_users_configured(self):
        """A real app built with users refuses an unauthenticated page request
        but lets a valid login through to the Dash page content."""
        import data
        from tests.conftest import (
            SAMPLE_PGN,
            preserve_dash_callbacks,
            stub_ui_sources,
        )

        data.reset()
        with stub_ui_sources(SAMPLE_PGN), mock.patch("data.sync_user"):
            from app import build_app
            _dash_app, server = build_app(
                ["teststudy"], player_name="Test Player",
                users=_users(), secret_key="test-secret",
            )
        # build_app has populated Dash's global callback list; snapshot it now so
        # the requests below (which drain it) can't steal ui_app's callbacks.
        # https base_url: build_app sets Secure cookies (not DEBUG), so the
        # session only round-trips over HTTPS — the way Fly serves it.
        https = {"base_url": "https://localhost"}
        with preserve_dash_callbacks(), mock.patch("data.sync_user"):
            client = server.test_client()
            # Unauthenticated → bounced to login, never the page
            assert client.get("/", **https).status_code == 302
            # The login page itself is reachable pre-auth
            assert client.get("/login", **https).status_code == 200
            # Valid login → the Dash index renders
            client.post("/login", data={"username": "daniel", "password": "hunter2"},
                        **https)
            assert client.get("/", **https).status_code == 200
        data.reset()

    def test_built_app_is_ungated_without_users(self):
        """With no users the dashboard runs as before — no login gate."""
        import data
        from tests.conftest import (
            SAMPLE_PGN,
            preserve_dash_callbacks,
            stub_ui_sources,
        )

        data.reset()
        with stub_ui_sources(SAMPLE_PGN):
            from app import build_app
            _dash_app, server = build_app(["teststudy"], player_name="Test Player")
        with preserve_dash_callbacks():
            assert server.test_client().get("/").status_code == 200
        data.reset()


# ---------------------------------------------------------------------------
# Per-user isolation through the gated app (issues #71 + #72)
# ---------------------------------------------------------------------------

class TestGatedIsolation:
    def test_each_logged_in_user_activates_their_own_store(self, tmp_path):
        """Through the real gated server, the request's authenticated user is
        the store every accessor resolves to — never another user's."""
        import pandas as pd

        import data
        from tests.conftest import preserve_dash_callbacks

        users = _users()  # daniel + friend
        data.reset()
        with mock.patch("data.sync_user"):  # don't really Sync at build
            from app import build_app
            _dash_app, server = build_app([], users=users, secret_key="test-secret")
        # Give the two stores distinct, recognisable data.
        data._registry["daniel"].df = pd.DataFrame({"ChapterURL": ["d1"]})
        data._registry["daniel"].initialized = True
        data._registry["friend"].df = pd.DataFrame({"ChapterURL": ["f1", "f2"]})
        data._registry["friend"].initialized = True

        try:
            # build_app populated Dash's global callback list; snapshot it so the
            # requests below (which drain it) don't steal ui_app's callbacks.
            with preserve_dash_callbacks():
                # The before_request hook resolves the session user and activates
                # that user's store — so each user's accessor reads only theirs.
                for username, expected in [("daniel", ["d1"]), ("friend", ["f1", "f2"])]:
                    with server.test_request_context("/"):
                        from flask import session
                        session["user"] = username
                        server.preprocess_request()
                        assert list(data.get_df()["ChapterURL"]) == expected
        finally:
            data.reset()
