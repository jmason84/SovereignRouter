#!/usr/bin/env python3
"""Hugging Face Inference Providers catalog source (v0.10.7, 2026-11-27).

Read-only discovery and price-drift input built on HF's PUBLIC router
catalog, ``GET https://router.huggingface.co/v1/models``. The endpoint
needs no credential and this module never sends one (no Authorization
header, no token read from disk or env). It writes nothing; the two
host tools (``discovery_tripwire.py`` and ``price_check.py``) opt in via
``--hf-file`` / ``--hf-url`` and keep their own report-only contracts.

The catalog lists, per model, every provider that serves it with live
status, per-million-token input/output price, context length and a
tool-support flag. That makes it a cheap second opinion beside the
LiteLLM cost map, and it prices the exact route the HF registry
entries use (a ``:<backend>`` pinned model string).

F-04 stands: aggregators nominate, providers decide. A drift row here
is a verification task; a nomination starts the existing gated
pipeline (terms review -> probe -> disabled entry -> battery ->
owner's gate). Neither changes the registry.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any, Optional

DEFAULT_HF_URL = "https://router.huggingface.co/v1/models"
POLICY_SUFFIXES = {"fastest", "cheapest", "preferred"}


def load_hf_catalog(path: Optional[str] = None,
                    url: Optional[str] = None) -> list[dict[str, Any]]:
    """Return the catalog's ``data`` list. ``path`` wins (offline and
    tests). The URL fetch sends only a User-Agent."""
    if path:
        raw = Path(path).read_text(encoding="utf-8")
    else:
        req = urllib.request.Request(
            url or DEFAULT_HF_URL,
            headers={"User-Agent": "sovereign-hf-catalog"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
    data = json.loads(raw)
    models = data.get("data") if isinstance(data, dict) else data
    if not isinstance(models, list):
        raise ValueError("HF catalog has no data list")
    return models


def split_route(model: str) -> tuple[str, Optional[str]]:
    """``org/Name:deepinfra`` -> (``org/Name``, ``deepinfra``). A policy
    suffix (:fastest / :cheapest) is not a backend and returns None."""
    if ":" not in model:
        return model, None
    base, _, suffix = model.rpartition(":")
    if suffix.lower() in POLICY_SUFFIXES:
        return base, None
    return base, suffix.lower()


def index_catalog(catalog: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {m["id"].lower(): m for m in catalog if m.get("id")}


def provider_row(model: dict[str, Any], slug: str) -> Optional[dict[str, Any]]:
    for p in model.get("providers") or []:
        if str(p.get("provider", "")).lower() == slug:
            return p
    return None


def live_providers(model: dict[str, Any]) -> list[dict[str, Any]]:
    return [p for p in model.get("providers") or []
            if p.get("status") == "live"]


def cheapest_live(model: dict[str, Any]) -> Optional[dict[str, Any]]:
    rows = [p for p in live_providers(model) if p.get("pricing")]
    if not rows:
        return None
    return min(rows, key=lambda p: (
        float((p["pricing"] or {}).get("input") or 0)
        + float((p["pricing"] or {}).get("output") or 0)))


def compare_route(entry: dict[str, Any], idx: dict[str, dict[str, Any]],
                  tolerance: float) -> Optional[dict[str, Any]]:
    """Compare one registry entry with the HF listing of the backend
    that serves it. Applies to (a) provider ``huggingface`` entries
    (backend = pinned suffix) and (b) entries whose own provider slug
    is also an HF backend for that model (deepinfra, novita, ...).
    Returns None when HF does not list the model/backend."""
    base, backend = split_route(entry.get("model", ""))
    model = idx.get(base.lower())
    if model is None:
        return None
    if entry.get("provider") == "huggingface":
        if backend is None:
            return None
        slug = backend
    else:
        slug = str(entry.get("provider", "")).lower()
    row = provider_row(model, slug)
    if row is None:
        return None
    pricing = row.get("pricing") or {}
    fields: dict[str, Any] = {}
    drift = False
    for reg_field, hf_field in (("input_usd_per_million", "input"),
                                ("output_usd_per_million", "output")):
        reg_val, hf_val = entry.get(reg_field), pricing.get(hf_field)
        if reg_val is None or hf_val is None:
            continue
        reg_f, hf_f = float(reg_val), float(hf_val)
        delta = abs(hf_f - reg_f)
        rel = delta / reg_f if reg_f else (0.0 if delta == 0 else 1.0)
        fd = rel > tolerance and delta > 0.0005
        drift = drift or fd
        fields[reg_field] = {"registry": reg_f, "hf": hf_f, "drift": fd}
    status = row.get("status")
    return {"id": entry["id"], "hf_model": model["id"], "backend": slug,
            "hf_status": status, "not_live": status != "live",
            "fields": fields, "drift": drift,
            "context_length": row.get("context_length"),
            "supports_tools": row.get("supports_tools")}


def run_hf_check(registry: dict[str, Any], catalog: list[dict[str, Any]],
                 tolerance: float = 0.01) -> dict[str, Any]:
    idx = index_catalog(catalog)
    rows = []
    for e in registry.get("models", []):
        r = compare_route(e, idx, tolerance)
        if r is not None:
            rows.append(r)
    return {"catalog_models": len(catalog), "compared": len(rows),
            "drifted": [r for r in rows if r["drift"]],
            "not_live": [r for r in rows if r["not_live"]],
            "rows": rows}


def render_hf_section(summary: dict[str, Any]) -> str:
    lines = ["## Hugging Face router catalog cross-check", "",
             f"Catalog models: {summary['catalog_models']} - compared: "
             f"{summary['compared']} - drifted: {len(summary['drifted'])} "
             f"- backend not live: {len(summary['not_live'])}", "",
             "VERIFICATION TASKS only (F-04). A direct-provider entry may "
             "differ from HF's pass-through because of provider discounts.",
             ""]
    for r in summary["drifted"]:
        lines.append(f"### {r['id']} (HF {r['hf_model']} via {r['backend']})")
        for field, v in r["fields"].items():
            if v["drift"]:
                lines.append(f"- {field}: registry {v['registry']} vs "
                             f"HF {v['hf']} per million")
        lines.append("")
    for r in summary["not_live"]:
        lines.append(f"- NOT LIVE: {r['id']} backend {r['backend']} is "
                     f"'{r['hf_status']}' on HF")
    return "\n".join(lines) + "\n"


def known_model_keys(registry: dict[str, Any]) -> set[str]:
    known: set[str] = set()
    for e in registry.get("models", []):
        known.add(e["id"].lower())
        if e.get("model"):
            base, _ = split_route(e["model"])
            known.add(base.lower())
    return known


def nominate_from_hf(catalog: list[dict[str, Any]], known: set[str],
                     only_tools: bool = False) -> list[dict[str, Any]]:
    """HF catalog models the registry does not carry, with the cheapest
    live provider's price/context/tools as provenance. ``known`` holds
    lower-cased ids (see known_model_keys)."""
    out: list[dict[str, Any]] = []
    for m in sorted(catalog, key=lambda x: x.get("id", "")):
        mid = m.get("id", "")
        if not mid or mid.lower() in known:
            continue
        live = live_providers(m)
        if not live:
            continue
        if only_tools and not any(p.get("supports_tools") for p in live):
            continue
        cheap = cheapest_live(m)
        pr = (cheap or {}).get("pricing") or {}
        out.append({
            "model_key": mid, "source": "hf-router-catalog",
            "live_providers": len(live),
            "cheapest_provider": (cheap or {}).get("provider"),
            "input_usd_per_million": pr.get("input"),
            "output_usd_per_million": pr.get("output"),
            "max_context": max((p.get("context_length") or 0)
                               for p in live) or None,
            "supports_tools": any(p.get("supports_tools") for p in live),
            "modalities_in": (m.get("architecture") or {}).get(
                "input_modalities"),
        })
    return out
