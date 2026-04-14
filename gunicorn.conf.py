"""
gunicorn.conf.py
================
Gunicorn configuration for production deployment.
Loaded automatically when gunicorn is started from the project root.
"""
import multiprocessing
import os

# Bind
bind = f"0.0.0.0:{os.environ.get('PORT', '8050')}"

# Workers: 2 × CPU cores + 1 is the standard formula for I/O-bound apps.
# For a personal dashboard, 2 workers is plenty.
workers = int(os.environ.get("WEB_CONCURRENCY", min(2, multiprocessing.cpu_count() + 1)))
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
