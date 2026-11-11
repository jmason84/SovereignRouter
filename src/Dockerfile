# Sovereign Router v0.5 gateway image.
# Hardened per spec "Deployment": read-only rootfs compatible (all state
# lives in /state, mounted at runtime), non-root, no secrets in the image.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SOVEREIGN_STATE_DIR=/state

WORKDIR /app
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt

# --chmod: the container runs as uid 10001; build-context files with
# restrictive modes (e.g. 660) are unreadable to it (deploy fix, 2026-11-02).
COPY --chmod=644 *.py ./
COPY --chmod=644 registry.json ./

RUN useradd --uid 10001 --no-create-home sovereign \
    && mkdir -p /state && chown sovereign:sovereign /state
USER sovereign

EXPOSE 6075
# Compose/stack must add: read_only: true, cap_drop: [ALL],
# security_opt: [no-new-privileges:true], tmpfs for /tmp, and a bind
# mount of the state dir at /state (keys/ inside it are mode-600 files).
HEALTHCHECK --interval=30s --timeout=5s \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:6075/health',timeout=4)"
CMD ["uvicorn", "gateway:create_app", "--factory", "--host", "0.0.0.0", "--port", "6075"]
