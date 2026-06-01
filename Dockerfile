FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Default environment (override at runtime)
ENV LICHESS_STUDY_IDS="6jYtXHGp" \
    PLAYER_NAME="" \
    PORT=8050

EXPOSE 8050

CMD gunicorn app:server \
    --bind 0.0.0.0:$PORT \
    --workers 2 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -
