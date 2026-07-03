# Prune Open WebUI standalone image
# Build on official Open WebUI image so all deps available
FROM ghcr.io/open-webui/open-webui:main

# Copy prune tool into /app/prune inside container
# Repo is designed to be cloned under open-webui/prune, but in this image
# we place it at /app/prune and run from /app like README instructions.
WORKDIR /app
COPY . /app/prune

# Optional: install extra prune-only deps (rich, dev tools). Safe if already present.
RUN pip install --no-cache-dir -r /app/prune/requirements.txt || true

# Default: non-interactive dry-run (safe preview). Override with your own args.
ENTRYPOINT ["python", "/app/prune/prune.py"]
CMD ["--days", "90", "--dry-run"]
