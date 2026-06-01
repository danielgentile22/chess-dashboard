"""
app.py
======
Entry point for the Chess Stats Dashboard.

Local development
-----------------
    python app.py --pgn "USCF OTB FULL.pgn" [--player "Gentile, Daniel"]

Gunicorn / Render deployment
-----------------------------
Set environment variables PGN_PATH (and optionally PLAYER_NAME), then:

    gunicorn app:server --bind 0.0.0.0:$PORT

``server`` is the Flask WSGI object exposed at module level.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

import dash_bootstrap_components as dbc

import data
from config import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def build_app(pgn_path: str, player_name=None):
    """Parse the PGN, build the Dash app, and return (dash_app, server)."""
    from dash import Dash

    from callbacks import register_callbacks
    from layout import make_layout

    df, detected = data.initialize(pgn_path, player_name=player_name)

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


# ---------------------------------------------------------------------------
# Module-level server for gunicorn
# ---------------------------------------------------------------------------

server = None

if config.PGN_PATH:
    if not os.path.exists(config.PGN_PATH):
        logger.error("PGN file not found: %r", config.PGN_PATH)
        sys.exit(1)
    _app, server = build_app(config.PGN_PATH, player_name=config.PLAYER_NAME)


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Chess Stats Dashboard")
    ap.add_argument("--pgn",    required=True)
    ap.add_argument("--player", default=None)
    ap.add_argument("--host",   default=config.HOST)
    ap.add_argument("--port",   default=config.PORT, type=int)
    ap.add_argument("--debug",  action="store_true", default=config.DEBUG)
    args = ap.parse_args()

    if not os.path.exists(args.pgn):
        raise FileNotFoundError(f"PGN file not found: {args.pgn!r}")

    dash_app, _ = build_app(args.pgn, player_name=args.player)
    logger.info("Dashboard ready at http://%s:%d/", args.host, args.port)
    dash_app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
