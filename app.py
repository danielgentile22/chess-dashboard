"""
app.py
======
Entry point for the Chess Dashboard.

Local development
-----------------
    python app.py --study 6jYtXHGp [--study abcd1234] [--player "Gentile, Daniel"]

Gunicorn / Render deployment
-----------------------------
Set environment variable LICHESS_STUDY_IDS (comma-separated study IDs, and
optionally PLAYER_NAME), then:

    gunicorn app:server --bind 0.0.0.0:$PORT

``server`` is the Flask WSGI object exposed at module level.
"""
from __future__ import annotations

import argparse
import logging
import sys

import dash_bootstrap_components as dbc

import data
from config import config
from sync import SyncError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _index_template(root_block: str) -> str:
    """The Dash HTML index template with the generated theme tokens inlined.

    The ``{%...%}`` placeholders are Dash's own — it fills in metas, the
    component CSS/JS bundles, and the React entry point.  We add one
    ``<style>`` tag carrying the ``:root`` variable block generated from
    ``styles.THEME`` so the browser palette comes from the same Python
    definition the Plotly charts use.
    """
    return (
        "<!DOCTYPE html>\n"
        "<html>\n"
        "    <head>\n"
        "        {%metas%}\n"
        "        <title>{%title%}</title>\n"
        "        {%favicon%}\n"
        "        {%css%}\n"
        '        <style id="cs-theme-tokens">\n'
        f"{root_block}"
        "        </style>\n"
        "    </head>\n"
        "    <body>\n"
        "        {%app_entry%}\n"
        "        <footer>\n"
        "            {%config%}\n"
        "            {%scripts%}\n"
        "            {%renderer%}\n"
        "        </footer>\n"
        "    </body>\n"
        "</html>"
    )


def build_app(study_ids: list[str], player_name=None, token=None, cache_path=None,
              uscf_member_id=None, uscf_cache_path=None,
              anthropic_api_key=None, analysis_cache_path=None,
              users=None, secret_key=None):
    """Sync the designated Studies, build the Dash app, and return (dash_app, server).

    When *users* is a non-empty multi-user config (PRD #55), the whole server is
    gated behind a login (issue #71 [G1]) and the data store becomes per-user
    (issue #72 [G2]): each user is Synced against their own Studies / token /
    USCF member ID, and the request's authenticated user is activated before any
    page renders.  With no users it runs ungated, single-user, exactly as before.
    """
    from dash import Dash

    if users:
        # Multi-user (issue #72): a store per user, Synced eagerly against each
        # user's own config; the per-request hook below activates the right one.
        data.register_users(
            users, data_dir=config.DATA_DIR, anthropic_api_key=anthropic_api_key,
        )
        for username in users:
            data.sync_user(username)
        detected = "Multi-user"
    else:
        _df, detected = data.initialize(
            study_ids, player_name=player_name, token=token, cache_path=cache_path,
            uscf_member_id=uscf_member_id, uscf_cache_path=uscf_cache_path,
            anthropic_api_key=anthropic_api_key,
            analysis_cache_path=analysis_cache_path,
        )

    # Creating the app imports every module in pages/ (each registers its own
    # route and callbacks), so the data store must be initialized first.
    dash_app = Dash(
        __name__,
        use_pages=True,
        external_stylesheets=[dbc.themes.CYBORG, dbc.icons.BOOTSTRAP],
        suppress_callback_exceptions=True,
        title=f"Chess Dashboard — {detected}",
        # Lichess's pgn-viewer (issue #60 [F6]) ships as an ES module; Dash would
        # otherwise inject it as a classic <script> and the browser would reject
        # its `export`.  Keep it out of the auto-bundle — assets/lpv-init.js
        # imports it dynamically.  It is still served at /assets/ on request.
        assets_ignore=r"lichess-pgn-viewer\.min\.js",
    )

    # Inject the theme tokens as a CSS :root block generated from the single
    # Python definition in styles.THEME — so chart colors and CSS variables
    # can never drift apart (assets/custom.css consumes var(--cs-*)).
    import styles
    dash_app.index_string = _index_template(styles.css_root_block())

    # Layout as a *function*: every browser page load rebuilds the shell from
    # the current data store, so header stats and filter options stay fresh.
    import shell
    dash_app.layout = shell.make_shell

    @dash_app.server.route("/health")
    def health():
        return "ok", 200

    # The login gate (issue #71 [G1]) + per-request user activation (issue #72
    # [G2]).  Installed only when users are configured; coach material is
    # private, so a gated server is the only place it may render.
    if users:
        import auth
        auth.install_auth(
            dash_app.server, users, secret_key=secret_key or config.SECRET_KEY,
        )

        # Runs after the auth gate (registration order): once a request is past
        # the gate, resolve its authenticated user so every accessor reads that
        # user's store.  An unauthenticated request is redirected by the gate
        # before reaching here.
        @dash_app.server.before_request
        def _activate_request_user():
            user = auth.current_user()
            data.activate(user)
            if user:
                data.ensure_synced(user)

    return dash_app, dash_app.server


def _exit_with_sync_error(exc: Exception, study_label: str) -> None:
    """Log a clear, actionable startup error and exit."""
    logger.error("Could not Sync from Lichess: %s", exc)
    logger.error(
        "Check the study IDs (%s) and your network connection.",
        study_label or "<not set>",
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Module-level server for gunicorn
# ---------------------------------------------------------------------------

server = None

# Boot when either a single global Study list (single-user) or a multi-user
# config (PRD #55) is present.  Multi-user needs no global STUDY_IDS — each
# user's Studies live in their own config record.
if config.STUDY_IDS or config.USERS:
    try:
        _app, server = build_app(
            config.STUDY_IDS,
            player_name=config.PLAYER_NAME,
            token=config.LICHESS_API_TOKEN,
            cache_path=config.CACHE_PATH,
            uscf_member_id=config.USCF_MEMBER_ID,
            uscf_cache_path=config.USCF_CACHE_PATH,
            anthropic_api_key=config.ANTHROPIC_API_KEY,
            analysis_cache_path=config.ANALYSIS_CACHE_PATH,
            users=config.USERS,
            secret_key=config.SECRET_KEY,
        )
    except (SyncError, RuntimeError) as _exc:
        _exit_with_sync_error(_exc, f"LICHESS_STUDY_IDS={config.STUDY_IDS!r}")


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Chess Dashboard")
    ap.add_argument(
        "--study",
        action="append",
        dest="studies",
        default=None,
        help="Lichess study ID to Sync games from (repeat for multiple studies); "
             "defaults to LICHESS_STUDY_IDS",
    )
    ap.add_argument("--player", default=config.PLAYER_NAME)
    ap.add_argument("--token",  default=config.LICHESS_API_TOKEN,
                    help="Lichess API token (only needed for private studies)")
    ap.add_argument("--cache",  default=config.CACHE_PATH,
                    help="PGN cache file for offline fallback (default: games.pgn)")
    ap.add_argument("--uscf-member", default=config.USCF_MEMBER_ID, dest="uscf_member_id",
                    help="USCF member ID whose record enriches the Games "
                         "(defaults to USCF_MEMBER_ID; omit to run Lichess-only)")
    ap.add_argument("--uscf-cache", default=config.USCF_CACHE_PATH, dest="uscf_cache_path",
                    help="USCF response cache for offline fallback "
                         "(default: uscf_cache.json)")
    ap.add_argument("--analysis-cache", default=config.ANALYSIS_CACHE_PATH,
                    dest="analysis_cache_path",
                    help="AI-summary cache so unchanged games aren't re-billed "
                         "(default: analysis_cache.json)")
    ap.add_argument("--host",   default=config.HOST)
    ap.add_argument("--port",   default=config.PORT, type=int)
    ap.add_argument("--debug",  action="store_true", default=config.DEBUG)
    args = ap.parse_args()

    study_ids = args.studies or config.STUDY_IDS
    if not study_ids:
        ap.error("at least one --study (or LICHESS_STUDY_IDS) is required")

    try:
        dash_app, _ = build_app(
            study_ids, player_name=args.player, token=args.token, cache_path=args.cache,
            uscf_member_id=args.uscf_member_id, uscf_cache_path=args.uscf_cache_path,
            anthropic_api_key=config.ANTHROPIC_API_KEY,
            analysis_cache_path=args.analysis_cache_path,
        )
    except (SyncError, RuntimeError) as exc:
        _exit_with_sync_error(exc, f"--study {study_ids!r}")

    logger.info("Dashboard ready at http://%s:%d/", args.host, args.port)
    dash_app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
