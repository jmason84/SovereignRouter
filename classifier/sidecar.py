#!/usr/bin/env python3
"""
Sovereign Router v0.5 — Difficulty Classifier Sidecar (Phase 2 deliverable)

Layers (spec v0.5.3, "Difficulty classifier"):
  1. Rules            — implemented here for benchmarking parity; in production
                        the canonical copy lives in the GATEWAY. Clear cases
                        only (explicit/clear-cut patterns), <1 ms.
  2. Embedding        — nearest-match against examples.jsonl. Prefers
                        sentence-transformers (bge-small / all-MiniLM class,
                        ~30M params) when installed; otherwise falls back to a
                        dependency-free TF-IDF + hash embedding with the SAME
                        interface, so the service runs anywhere. /health says
                        which path is active. Majority (similarity-weighted)
                        vote over the closest 7 examples -> level + task class
                        + confidence. Input = last user message, truncated to
                        1,500 chars (spec).
  3. Arch-Router-1.5B — HTTP client stub to a llama.cpp server (env LLAMA_URL).
                        DISABLED by default (ARCH_ROUTER_ENABLED=1 to enable).
                        In-path only if the Phase 2 benchmark on VM103 shows
                        p95 < 1.2 s on 2 CPUs; otherwise use offline-label mode
                        (POST /label) for the learning loop.

Contract (gateway -> sidecar):
  POST /classify {"text": "...", "client": "hermes"}
      -> {"level": 1-5, "task_class": "chat|infra|code|reasoning|long-context",
          "confidence": 0.0-1.0, "layer": "rules|embedding|arch-router",
          "latency_ms": float}
  GET  /health    -> status, active embedder, bank size, arch-router state
  GET  /benchmark -> stub: last benchmark.py results if benchmark-results.json
                     exists next to this file, else a "not run yet" stub.
  POST /label     -> offline Arch-Router labeling (never used in-path unless
                     the benchmark gate passes; 501 when arch-router disabled)

The gateway enforces a 1.5 s timeout on /classify and falls back to its own
rules layer on timeout/error (spec). This service therefore never needs to be
on the critical path for correctness — only for quality of the level choice.

No prompt text is logged by this service. Example bank stores labels only for
production-grown examples (Phase 4); the seed bank in examples.jsonl is
hand-written synthetic text.

Run:  python3 sidecar.py            (stdlib HTTP server, port 8080)
      uvicorn sidecar:app           (if FastAPI is installed, same behaviour)
Env:  PORT, EXAMPLES_PATH, EMBED_MODEL, CLASSIFIER_EMBEDDER=auto|st|tfidf,
      LLAMA_URL, ARCH_ROUTER_ENABLED=0|1, ARCH_ROUTER_MIN_CONFIDENCE
"""

import json
import math
import os
import re
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXAMPLES_PATH = Path(os.environ.get("EXAMPLES_PATH", HERE / "examples.jsonl"))
ROUTE_DESCRIPTIONS_PATH = HERE / "route_descriptions.json"
BENCHMARK_RESULTS_PATH = HERE / "benchmark-results.json"
TRUNCATE_CHARS = 1500          # spec: embed last user message truncated to 1,500 chars
K_NEIGHBORS = 7                # spec: majority vote over closest 7
LEVELS = (1, 2, 3, 4, 5)
TASK_CLASSES = ("chat", "infra", "code", "reasoning", "long-context")

# --------------------------------------------------------------------------
# Text normalisation / tokenisation (shared by both embedders' fallback path)
# --------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._/-]*")


def tokenize(text: str) -> list:
    """Lowercase word tokens plus word bigrams (phrases like 'root cause')."""
    words = _TOKEN_RE.findall(text.lower())
    toks = list(words)
    toks += [f"{a} {b}" for a, b in zip(words, words[1:])]
    return toks


# --------------------------------------------------------------------------
# Embedder backends — identical interface: fit(texts), embed(text) -> vector
# Vectors are plain lists of floats, L2-normalised; cosine = dot product.
# --------------------------------------------------------------------------

class TfidfHashEmbedder:
    """Dependency-free fallback: hashed TF-IDF over tokens+bigrams.

    Deterministic, no model download, ~ms per text. Weaker semantically than
    a transformer embedder (no paraphrase generalisation beyond shared
    vocabulary) — benchmark.py numbers on this path are a FLOOR for the real
    bge-small/MiniLM path, not the target.
    """

    name = "tfidf-hash"
    dim = 4096

    def __init__(self, dim: int = 4096):
        self.dim = dim
        self.idf = {}

    @staticmethod
    def _hash(tok: str) -> int:
        # Stable FNV-1a (hash() is salted per-process; must be reproducible)
        h = 2166136261
        for ch in tok.encode("utf-8"):
            h = ((h ^ ch) * 16777619) & 0xFFFFFFFF
        return h

    def fit(self, texts):
        df = Counter()
        for t in texts:
            for tok in set(tokenize(t)):
                df[tok] += 1
        n = max(len(texts), 1)
        self.idf = {tok: math.log((n + 1) / (c + 1)) + 1.0 for tok, c in df.items()}
        self._default_idf = math.log(n + 1) + 1.0
        return self

    def embed(self, text: str):
        vec = [0.0] * self.dim
        counts = Counter(tokenize(text))
        for tok, c in counts.items():
            w = (1.0 + math.log(c)) * self.idf.get(tok, self._default_idf)
            vec[self._hash(tok) % self.dim] += w
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


class SentenceTransformerEmbedder:
    """Preferred path: bge-small / all-MiniLM class model via sentence-transformers."""

    name = "sentence-transformers"

    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer  # optional dep

        self.model_name = model_name
        self._model = SentenceTransformer(model_name)
        self.name = f"sentence-transformers:{model_name}"

    def fit(self, texts):  # nothing to fit; kept for interface parity
        return self

    def embed(self, text: str):
        v = self._model.encode([text], normalize_embeddings=True)[0]
        return [float(x) for x in v]


def make_embedder(bank_texts):
    """auto: sentence-transformers if importable, else TF-IDF fallback."""
    pref = os.environ.get("CLASSIFIER_EMBEDDER", "auto").lower()
    if pref in ("auto", "st"):
        try:
            model = os.environ.get("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
            emb = SentenceTransformerEmbedder(model)
            emb.fit(bank_texts)
            return emb
        except Exception:
            if pref == "st":
                raise
    emb = TfidfHashEmbedder()
    emb.fit(bank_texts)
    return emb


# --------------------------------------------------------------------------
# Layer 1 — rules (mirror of the gateway rules; combined keyword+verb logic)
# Only returns a decision when the case is CLEAR (confidence >= threshold);
# otherwise returns None and the embedding layer decides.
# --------------------------------------------------------------------------

INFRA_TERMS = {
    "docker", "container", "containers", "proxmox", "vm", "vms", "backup",
    "backups", "snapshot", "nginx", "dns", "firewall", "portainer", "n8n",
    "server", "stack", "service", "systemd", "ssh", "disk", "cpu", "ram",
    "network", "vlan", "router", "gateway", "database", "postgres", "mysql",
    "redis", "hestia", "cloudflare", "tunnel", "vm103", "vm102", "vm112",
    "ct100", "lxc", "zfs", "raid", "cron", "log", "logs", "port", "ports",
    "host", "hosts", "tunnel", "endpoint",
}
CHANGE_VERBS = {
    "deploy", "migrate", "migration", "delete", "remove", "restore",
    "rotate", "restart", "reboot", "upgrade", "update", "install",
    "uninstall", "rebuild", "reconfigure", "configure", "move", "switch",
    "cutover", "rollback", "provision", "wipe", "resize", "scale", "patch",
}
DIAGNOSIS_VERBS = {
    "debug", "fix", "why", "root", "cause", "diagnose", "troubleshoot",
    "failing", "failed", "fails", "failure", "error", "broken", "crash", "crashed",
    "down", "slow", "leak", "investigate",
}
CODE_TERMS = {
    "script", "function", "code", "python", "bash", "refactor", "regex",
    "bug", "compile", "test", "tests", "api", "json", "parse", "class",
    "endpoint", "repository", "repo", "git", "branch", "merge", "version",
    "dockerfile",
}
LONG_CONTEXT_TERMS = {
    "entire", "whole", "all of", "full transcript", "complete log",
    "every file", "the repository", "this document", "attached",
    "long document", "book", "chapter",
}
CRITICAL_TERMS = {
    "firewall", "credential", "credentials", "password rotation",
    "rotate the", "api key", "api keys", "secret", "secrets", "cutover",
    "production cutover", "security audit", "penetration", "iptables",
    "access control", "certificate authority", "root access",
}
GREETING_RE = re.compile(
    r"^\s*(hi|hello|hey|good (morning|afternoon|evening)|thanks|thank you|"
    r"ok|okay|got it|perfect|great|nice|cool|bye|goodbye|good night)[\s!.]*$",
    re.IGNORECASE,
)
FORMAT_RE = re.compile(
    r"(bullet list|numbered list|table|markdown|capitali[sz]e|uppercase|"
    r"lowercase|reformat|rewrite this (shorter|simpler)|make this a list|"
    r"one[- ]line version|shorten this|spell out)",
    re.IGNORECASE,
)


def guess_task_class(text_l: str, words: set) -> str:
    if any(t in text_l for t in LONG_CONTEXT_TERMS) or len(text_l) > 1200:
        return "long-context"
    infra_hits = len(words & INFRA_TERMS)
    code_hits = len(words & CODE_TERMS)
    if infra_hits >= 1 and infra_hits >= code_hits:
        return "infra"
    if code_hits >= 1:
        return "code"
    if words & {"why", "compare", "plan", "decide", "tradeoff", "trade-off",
                "strategy", "evaluate", "recommend", "should"}:
        return "reasoning"
    return "chat"


def rules_classify(text: str):
    """Return (level, task_class, confidence) for clear cases, else None."""
    t = text.strip()
    tl = t.lower()
    words = set(_TOKEN_RE.findall(tl))

    # Explicit premium / critical: security & production-irreversible language.
    # Requires an action/planning context — merely EXPLAINING a firewall rule
    # or a credential is not a level-5 change.
    if any(term in tl for term in CRITICAL_TERMS) and (
        words & CHANGE_VERBS or words & {"plan", "audit", "design", "policy"}
        or "cutover" in tl or "lock down" in tl
    ):
        return 5, guess_task_class(tl, words), 0.90

    # Greetings / acknowledgements — trivial regardless of anything else
    if GREETING_RE.match(t) and len(t) < 80:
        return 1, "chat", 0.97

    # "Is the word X in this sentence/list ..." — a trivial text lookup,
    # even when X is an infrastructure term.
    if re.search(r"\bis the word \S+ in (this|the)\b", tl):
        return 1, "chat", 0.90

    # Pure formatting of supplied text — trivial. Not when the request also
    # asks for code/a script (e.g. "a script that prints a status table").
    if (
        FORMAT_RE.search(tl) and len(t) < 600
        and not (words & DIAGNOSIS_VERBS)
        and not (words & {"script", "function", "code"})
    ):
        return 1, "chat", 0.90

    # Whole-scope work: a scope word (entire / every / month of / full ...)
    # combined with an analysis or change action is multi-step by
    # construction — level 4, never a "short question".
    if re.search(r"\b(entire|every|all of|full|month of|week of|whole)\b", tl) and (
        words & {"analyze", "analyse", "map", "audit", "migrate", "review",
                 "through", "find", "evaluate", "compare"}
    ):
        return 4, guess_task_class(tl, words), 0.85

    # Very short simple questions / commands with no change or diagnosis verb
    # — this is the anti-over-classification rule: an infra MENTION alone
    # (e.g. "is docker running?") must NOT raise the level. Complexity
    # markers (analyze, plan, root cause, ...) disqualify a request from
    # being "simple" no matter how short it is.
    complexity_markers = {
        "analyze", "analyse", "plan", "design", "implement", "compare",
        "investigate", "evaluate", "root", "migrate", "refactor", "audit",
    }
    if (
        len(t) < 90
        and not (words & CHANGE_VERBS)
        and not (words & DIAGNOSIS_VERBS)
        and not (words & complexity_markers)
        and not (words & {"script", "function"})  # writing code is never L1/L2-by-rule
    ):
        if tl.endswith("?") or words & {"show", "list", "status", "check", "what", "is", "are"}:
            task = guess_task_class(tl, words)
            return (2 if task != "chat" else 1), task, 0.86

    # Obvious multi-step production change: infra + change verb + scope words
    if (words & INFRA_TERMS) and (words & CHANGE_VERBS) and any(
        w in tl for w in ("migrate", "cutover", "multi", "all ", "entire",
                          "production", "cluster", "zero downtime")
    ):
        return 4, "infra", 0.85

    return None  # not a clear case — defer to embedding layer


# --------------------------------------------------------------------------
# Layer 3 — Arch-Router via llama.cpp (stub client; disabled by default)
# --------------------------------------------------------------------------

class ArchRouterClient:
    """HTTP client for a llama.cpp server running Arch-Router-1.5B GGUF.

    Expected server: llama-server --port 8081 (see README for the command).
    Uses the OpenAI-compatible /v1/chat/completions endpoint llama.cpp offers.
    """

    def __init__(self):
        self.url = os.environ.get("LLAMA_URL", "http://127.0.0.1:8081").rstrip("/")
        self.enabled = os.environ.get("ARCH_ROUTER_ENABLED", "0") == "1"
        self._routes_prompt = self._build_routes_prompt()

    def _build_routes_prompt(self) -> str:
        try:
            routes = json.loads(ROUTE_DESCRIPTIONS_PATH.read_text())
            lines = [f"- {r['route']}: {r['description']}" for r in routes["routes"]]
            return "\n".join(lines)
        except Exception:
            return ""

    def available(self) -> bool:
        if not self.enabled:
            return False
        try:
            req = urllib.request.Request(self.url + "/health")
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                return resp.status == 200
        except Exception:
            return False

    def classify(self, text: str):
        """Return (level, task_class, confidence) or None on any failure."""
        if not self.enabled:
            return None
        prompt = (
            "Choose the single best route for the user request.\n"
            f"Routes:\n{self._routes_prompt}\n"
            f"Request: {text[:TRUNCATE_CHARS]}\n"
            'Answer with JSON only: {"route": "level-N", "task_class": "..."}'
        )
        body = json.dumps({
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0, "max_tokens": 64,
        }).encode()
        req = urllib.request.Request(
            self.url + "/v1/chat/completions", data=body,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                data = json.loads(resp.read())
            content = data["choices"][0]["message"]["content"]
            m = re.search(r"level[-_ ]?([1-5])", content)
            if not m:
                return None
            tc = "chat"
            for cand in TASK_CLASSES:
                if cand in content:
                    tc = cand
                    break
            return int(m.group(1)), tc, 0.80
        except Exception:
            return None


# --------------------------------------------------------------------------
# Classifier — orchestrates the layers
# --------------------------------------------------------------------------

class Classifier:
    def __init__(self, examples_path=EXAMPLES_PATH):
        self.examples = []
        with open(examples_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    self.examples.append(json.loads(line))
        texts = [e["text"][:TRUNCATE_CHARS] for e in self.examples]
        self.embedder = make_embedder(texts)
        self.bank_vectors = [self.embedder.embed(t) for t in texts]
        self.arch = ArchRouterClient()
        # Below this embedding confidence, consult Arch-Router when enabled.
        self.arch_min_conf = float(os.environ.get("ARCH_ROUTER_MIN_CONFIDENCE", "0.45"))

    # -- layer 2 ------------------------------------------------------------
    def embedding_classify(self, text: str):
        vec = self.embedder.embed(text[:TRUNCATE_CHARS])
        scored = []
        for ex, bv in zip(self.examples, self.bank_vectors):
            sim = sum(a * b for a, b in zip(vec, bv))
            scored.append((sim, ex))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:K_NEIGHBORS]

        level_weight = defaultdict(float)
        task_weight = defaultdict(float)
        total = 0.0
        for sim, ex in top:
            w = max(sim, 0.01) ** 2  # sharpen: near matches dominate the vote
            level_weight[ex["level"]] += w
            task_weight[ex["task_class"]] += w
            total += w
        # Tie / near-tie between adjacent levels >=3: pick the higher (spec:
        # under-classifying a hard task costs more than the price difference).
        best_level = max(level_weight.items(), key=lambda kv: kv[1])
        level = best_level[0]
        for other_level, w in level_weight.items():
            if other_level > level and other_level - level == 1 and w >= 0.85 * best_level[1]:
                level = other_level
                break
        task = max(task_weight.items(), key=lambda kv: kv[1])[0]
        vote_share = level_weight[level] / total if total else 0.0
        avg_sim = sum(s for s, _ in top) / len(top)
        confidence = round(min(0.99, 0.5 * vote_share + 0.5 * max(avg_sim, 0.0)), 3)
        return level, task, confidence, top

    # -- full pipeline --------------------------------------------------------
    def classify(self, text: str, client: str = "") -> dict:
        start = time.perf_counter()
        text = (text or "")[:TRUNCATE_CHARS]

        ruled = rules_classify(text)
        if ruled is not None:
            level, task, conf = ruled
            layer = "rules"
        else:
            level, task, conf, _ = self.embedding_classify(text)
            layer = "embedding"
            # Spec: "When the classifier is uncertain between two levels, it
            # picks the higher one for levels 3 to 5." Generalised: a
            # low-confidence embedding verdict at L2/L3 is bumped one level —
            # under-classifying hard work costs more than the price delta.
            # (Not applied at L4: an uncertain L4 bumped to L5 spends premium
            # money, the opposite failure; L4 uncertainty is left to the
            # adjacent-tie rule inside the vote and to output-check escalation.)
            if conf < 0.35 and 2 <= level <= 3:
                level += 1
            if conf < self.arch_min_conf and self.arch.enabled:
                arch = self.arch.classify(text)
                if arch is not None:
                    level, task, conf = arch
                    layer = "arch-router"

        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        return {
            "level": level,
            "task_class": task,
            "confidence": conf,
            "layer": layer,
            "latency_ms": latency_ms,
        }

    def health(self) -> dict:
        return {
            "status": "ok",
            "embedder": self.embedder.name,
            "example_bank": len(self.examples),
            "k_neighbors": K_NEIGHBORS,
            "arch_router": {
                "enabled": self.arch.enabled,
                "llama_url": self.arch.url,
                "mode": "in-path-if-enabled" if self.arch.enabled else "disabled (offline-label only)",
            },
        }


_CLASSIFIER = None


def get_classifier() -> Classifier:
    global _CLASSIFIER
    if _CLASSIFIER is None:
        _CLASSIFIER = Classifier()
    return _CLASSIFIER


# --------------------------------------------------------------------------
# HTTP surface — FastAPI when available, stdlib otherwise (identical routes)
# --------------------------------------------------------------------------

try:
    from fastapi import FastAPI
    from pydantic import BaseModel

    _HAVE_FASTAPI = True
    app = FastAPI(title="Sovereign Router Classifier Sidecar", version="0.5.0")

    class ClassifyRequest(BaseModel):
        text: str
        client: str = ""

    @app.post("/classify")
    def classify_endpoint(req: ClassifyRequest):
        return get_classifier().classify(req.text, req.client)

    @app.get("/health")
    def health_endpoint():
        return get_classifier().health()

    @app.get("/benchmark")
    def benchmark_endpoint():
        if BENCHMARK_RESULTS_PATH.exists():
            return json.loads(BENCHMARK_RESULTS_PATH.read_text())
        return {"status": "not-run", "note": "Run benchmark.py offline; results land in benchmark-results.json"}

    @app.post("/label")
    def label_endpoint(req: ClassifyRequest):
        arch = get_classifier().arch
        if not arch.enabled:
            return {"status": "disabled", "note": "Offline labeling requires ARCH_ROUTER_ENABLED=1 and LLAMA_URL"}
        result = arch.classify(req.text)
        if result is None:
            return {"status": "unavailable", "note": "llama.cpp server not reachable or unparseable reply"}
        level, task, conf = result
        return {"level": level, "task_class": task, "confidence": conf, "layer": "arch-router-offline"}

except ImportError:  # stdlib fallback server — no third-party deps required
    _HAVE_FASTAPI = False
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # never log request text
            pass

        def _send(self, code, payload):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                self._send(200, get_classifier().health())
            elif self.path == "/benchmark":
                if BENCHMARK_RESULTS_PATH.exists():
                    self._send(200, json.loads(BENCHMARK_RESULTS_PATH.read_text()))
                else:
                    self._send(200, {"status": "not-run",
                                     "note": "Run benchmark.py offline; results land in benchmark-results.json"})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except Exception:
                self._send(400, {"error": "invalid json"})
                return
            if self.path == "/classify":
                self._send(200, get_classifier().classify(
                    payload.get("text", ""), payload.get("client", "")))
            elif self.path == "/label":
                arch = get_classifier().arch
                if not arch.enabled:
                    self._send(501, {"status": "disabled",
                                     "note": "Offline labeling requires ARCH_ROUTER_ENABLED=1 and LLAMA_URL"})
                    return
                result = arch.classify(payload.get("text", ""))
                if result is None:
                    self._send(502, {"status": "unavailable"})
                else:
                    level, task, conf = result
                    self._send(200, {"level": level, "task_class": task,
                                     "confidence": conf, "layer": "arch-router-offline"})
            else:
                self._send(404, {"error": "not found"})

    def main():
        port = int(os.environ.get("PORT", "8080"))
        get_classifier()  # warm the bank before accepting traffic
        ThreadingHTTPServer(("0.0.0.0", port), _Handler).serve_forever()
        return 0


if __name__ == "__main__":
    # Direct execution must serve on BOTH paths. Before 2026-11-02 this
    # guard lived inside the stdlib branch only, so with FastAPI installed
    # `python3 sidecar.py` defined the app and exited silently (found by
    # the live E2E harness: connection refused on /classify).
    if _HAVE_FASTAPI:
        import uvicorn

        get_classifier()  # warm the bank before accepting traffic
        uvicorn.run(app, host="0.0.0.0",
                    port=int(os.environ.get("PORT", "8080")))
    else:
        raise SystemExit(main())
