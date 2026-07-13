#!/bin/sh
# Runs as root: the Fly /data volume is root-owned on first attach, so make it
# writable by appuser before dropping privileges. No /data (e.g. compose) → skip.
set -e
if [ -d /data ]; then
  chown -R appuser:appuser /data
fi
exec gosu appuser "$@"
