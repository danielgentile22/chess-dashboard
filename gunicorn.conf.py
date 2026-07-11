"""
gunicorn.conf.py
================
Gunicorn configuration for production deployment.
Loaded automatically when gunicorn is started from the project root.
"""
import os

# Bind
bind = f"0.0.0.0:{os.environ.get('PORT', '8050')}"

# Workers: hardcoded to 1, not configurable (ADR 0006). The dashboard keeps
# Synced games in worker memory and the Sync button atomically swaps that
# in-memory data; with multiple workers only the worker that handled the Sync
# would see the new games, and the per-request thread-local user activation
# would break. This is a correctness constraint, not a tuning knob.
workers = 1
worker_class = "sync"
timeout = 120

# Logging
accesslog = "-"   # stdout
errorlog  = "-"   # stderr
loglevel  = os.environ.get("LOG_LEVEL", "info")
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s %(D)sµs'

# Lifecycle
graceful_timeout = 30
keepalive = 5
