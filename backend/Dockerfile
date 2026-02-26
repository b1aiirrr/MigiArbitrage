# ─────────────────────────────────────────────────
# MigiArbitrage Backend — Dockerfile
# Optimized for 2GB RAM Ubuntu VPS
# ─────────────────────────────────────────────────
FROM python:3.12-slim AS base

# Prevent Python from buffering stdout/stderr
ENV PYTHONUNBUFFERED=1
# Use malloc for better memory management in containers
ENV PYTHONMALLOC=malloc
# Disable hash randomization for reproducibility
ENV PYTHONHASHSEED=0

# Create non-root user
RUN groupadd -r migi && useradd -r -g migi -d /app -s /sbin/nologin migi

WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    rm -rf /root/.cache /tmp/*

# Copy application code
COPY . .

# Own the app directory
RUN chown -R migi:migi /app

# Switch to non-root user
USER migi

# Expose WebSocket server port
EXPOSE 8765

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import asyncio, websockets; asyncio.run(websockets.connect('ws://localhost:8765'))" || exit 1

# Run the scanner
CMD ["python", "main.py"]
