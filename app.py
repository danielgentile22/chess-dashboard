"""
app.py
======
Entry point for the Chess Stats Dashboard.

Local development
-----------------
    python app.py --study 6jYtXHGp [--player "Gentile, Daniel"]

Gunicorn / Render deployment
-----------------------------
Set environment variable LICHESS_STUDY_IDS (and optionally PLAYER_NAME), then:

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
from lichess_client import LichessError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def build_app(study_id: str, player_name=None, token=None):
    """Sync the Study, build the Dash app, and return (dash_app, server)."""
    from dash import Dash

    from callbacks import register_callbacks
    from layout import make_layout

    df, detected = data.initialize(study_id, player_name=player_name, token=token)

    dash_app = Dash(
        __name__,
        external_stylesheets=[dbc.themes.CYBORG],
        suppress_callback_exceptions=True,
        title=f"Chess Stats — {detected}",
    )
    dash_app.layout = make_layout(df, detected)
    register_callbacks(dash_app)

    @dash_app.server.route("/health")
    def health():
        return "ok", 200

    return dash_app, dash_app.server


def _exit_with_sync_error(exc: Exception, study_label: str) -> None:
    """Log a clear, actionable startup error and exit."""
    logger.error("Could not Sync from Lichess: %s", exc)
    logger.error(
        "Check the study ID (%s) and your network connection.",
        study_label or "<not set>",
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Module-level server for gunicorn
# ---------------------------------------------------------------------------

server = None

if config.STUDY_ID:
    try:
        _app, server = build_app(
            config.STUDY_ID,
            player_name=config.PLAYER_NAME,
            token=config.LICHESS_API_TOKEN,
        )
    except (LichessError, RuntimeError) as _exc:
        _exit_with_sync_error(_exc, f"LICHESS_STUDY_IDS={config.STUDY_ID!r}")


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Chess Stats Dashboard")
    ap.add_argument(
        "--study",
        default=config.STUDY_ID or None,
        required=not config.STUDY_ID,
        help="Lichess study ID to Sync games from (e.g. 6jYtXHGp)",
    )
    ap.add_argument("--player", default=config.PLAYER_NAME)
    ap.add_argument("--token",  default=config.LICHESS_API_TOKEN,
                    help="Lichess API token (only needed for private studies)")
    ap.add_argument("--host",   default=config.HOST)
    ap.add_argument("--port",   default=config.PORT, type=int)
    ap.add_argument("--debug",  action="store_true", default=config.DEBUG)
    args = ap.parse_args()

    try:
        dash_app, _ = build_app(args.study, player_name=args.player, token=args.token)
    except (LichessError, RuntimeError) as exc:
        _exit_with_sync_error(exc, f"--study {args.study!r}")

    logger.info("Dashboard ready at http://%s:%d/", args.host, args.port)
    dash_app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
