# Sovereign Router v0.5 — classifier sidecar
#
# Spec limits (v0.5.3, Deployment): sidecar container 3 GB memory, 2 CPUs.
# Enforce them in compose, not here — reference stanza for the VM103 stack:
#
#   classifier:
#     image: sovereign-router-classifier:v0.5.0
#     build: ./classifier
#     mem_limit: 3g
#     cpus: 2.0
#     pids_limit: 128
#     read_only: true
#     cap_drop: [ALL]
#     security_opt: [no-new-privileges:true]
#     networks: [router-internal]        # gateway only; no host port
#     environment:
#       PORT: "8080"
#       # CLASSIFIER_EMBEDDER: auto     # st (bge-small) if installed, else tfidf
#       # ARCH_ROUTER_ENABLED: "0"      # 1 only after the p95 < 1.2 s gate passes
#       # LLAMA_URL: http://127.0.0.1:8081
#
# The Arch-Router llama.cpp server runs as a SEPARATE process/container in
# the same stack (see README) so its 1.7 GB GGUF and CPU use stay inside the
# same 3 GB / 2 CPU envelope — measure both together in the Phase 2 benchmark.
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
# Install the preferred serving + embedding stack. For a minimal image
# (TF-IDF fallback only, ~180 MB), comment the next line out — sidecar.py
# detects what is importable at runtime and reports it in /health.
RUN pip install --no-cache-dir -r requirements.txt

COPY sidecar.py benchmark.py examples.jsonl held_out.jsonl route_descriptions.json ./

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2)"

# With fastapi+uvicorn installed this serves the FastAPI app; without them,
# `python3 sidecar.py` runs the identical routes on the stdlib server.
CMD ["sh", "-c", "python3 -c 'import fastapi' 2>/dev/null && exec uvicorn sidecar:app --host 0.0.0.0 --port 8080 --workers 1 || exec python3 sidecar.py"]
