# -bookworm pins the Debian release (not just floating slim): keeps the build
# reproducible and guarantees gosu stays in the default apt repo.
FROM python:3.11-slim-bookworm
WORKDIR /app
# gosu lets the entrypoint chown the (root-owned) Fly volume as root, then drop
# to a non-root user for the app itself.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 appuser
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN chown -R appuser:appuser /app
EXPOSE 8050
# Entrypoint starts as root only to chown /data, then execs the app as appuser.
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["gunicorn", "app:server", "--config", "gunicorn.conf.py"]
