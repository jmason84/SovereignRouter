#!/usr/bin/env python3
"""Discovery tripwire — candidate nominations for the vetting
pipeline (2026-11-24; litellm-vs-sovereign borrow #3).

Watches LiteLLM's day-0 surfaces for models the registry does not
carry yet and emits them as candidate NOMINATIONS. A nomination is
the start of the existing gated pipeline (documented → terms review
→ probe → disabled entry → battery → owner's gate) — this tool
changes no gate and writes nothing except an explicit ``--out``
report. ADD-11's endpoint-family gate still applies to every
nomination downstream.

Two sources, either or both:
  * ``--map-file/--map-url``: diff LiteLLM's cost map keys against
    the registry (entry ids + upstream model ids). New models land
    in the map on day 0, so a new key is a nomination. This is the
    deterministic source.
  * ``--feed-file/--feed-url``: parse the LiteLLM model changelog
    RSS/Atom feed; titles naming a model key that the registry does
    not carry are nominations, with the item's date and link kept as
    provenance.

The map carries thousands of keys (every provider variant LiteLLM
knows); nominations are therefore filtered to keys whose provider
prefix matches a provider the fleet already uses OR that carry no
prefix decision yet — pass ``--provider`` to narrow, or
``--all-providers`` to see the full firehose. Default: fleet
providers only, because a nomination nobody will ever vet is noise.

Usage:
    python3 tools/discovery_tripwire.py [--registry src/registry.json]
        [--map-file FILE | --map-url URL] [--feed-file FILE]
        [--provider NAME ...] [--all-providers] [--json] [--out F]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hf_catalog  # noqa: E402  (v0.10.7: HF router catalog source)

DEFAULT_MAP_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json")
DEFAULT_FEED_URL = "https://models.litellm.ai/changelog/rss.xml"


def load_json_url(url: str) -> dict[str, Any]:
    req = urllib.request.Request(
        url, headers={"User-Agent": "sovereign-discovery-tripwire"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def registry_known_ids(registry: dict[str, Any]) -> set[str]:
    known: set[str] = set()
    for e in registry.get("models", []):
        known.add(e["id"])
        if e.get("model"):
            known.add(e["model"])
            if e.get("provider"):
                known.add(f"{e['provider']}/{e['model']}")
    return known


def registry_providers(registry: dict[str, Any]) -> set[str]:
    return {e.get("provider", "") for e in registry.get("models", [])} - {""}


def nominate_from_map(litellm: dict[str, Any], known: set[str],
                      providers: Optional[set[str]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key in sorted(litellm):
        if key == "sample_spec" or key in known:
            continue
        prefix = key.split("/", 1)[0] if "/" in key else ""
        if providers is not None and prefix not in providers:
            continue
        entry = litellm[key] or {}
        out.append({
            "model_key": key,
            "source": "litellm-cost-map",
            "litellm_provider": entry.get("litellm_provider"),
            "mode": entry.get("mode"),
            "input_cost_per_token": entry.get("input_cost_per_token"),
            "output_cost_per_token": entry.get("output_cost_per_token"),
        })
    return out


_MODEL_KEY_RE = re.compile(r"\b([a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._/-]*"
                           r"|[a-z0-9][a-z0-9._-]*)\b")


def nominate_from_feed(feed_xml: str, known: set[str],
                       providers: Optional[set[str]]) -> list[dict[str, Any]]:
    """Parse RSS 2.0 or Atom. Each item whose title names at least one
    key-shaped token not in the registry contributes its FIRST such
    token as a nomination, with date + link as provenance. Only
    slashed provider/model tokens qualify: bare words in prose
    titles ("New model: ...", "... price updated") false-positive
    constantly, and a missed bare-name announcement costs nothing —
    the map diff catches the same model deterministically. Titles
    are prose, so this source is best-effort by design."""
    root = ET.fromstring(feed_xml)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items = root.findall(".//item")
    atom = False
    if not items:
        items = root.findall(".//atom:entry", ns)
        atom = True
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if atom:
            title_el = item.find("atom:title", ns)
            link_el = item.find("atom:link", ns)
            date_el = item.find("atom:updated", ns)
            link = link_el.get("href") if link_el is not None else None
        else:
            title_el = item.find("title")
            link_el = item.find("link")
            date_el = item.find("pubDate")
            link = link_el.text if link_el is not None else None
        title = title_el.text if title_el is not None else ""
        date = date_el.text if date_el is not None else None
        for token in _MODEL_KEY_RE.findall(title or ""):
            if "/" not in token:
                continue
            if token in known or token in seen:
                continue
            prefix = token.split("/", 1)[0] if "/" in token else ""
            if providers is not None and "/" in token \
                    and prefix not in providers:
                continue
            seen.add(token)
            out.append({"model_key": token, "source": "litellm-changelog",
                        "title": title, "date": date, "link": link})
            break
    return out


def render_report(nominations: list[dict[str, Any]]) -> str:
    lines = [
        "# Discovery tripwire — candidate nominations",
        "",
        f"{len(nominations)} nomination(s). Each enters the existing "
        "gated pipeline (documented → terms review → probe → disabled "
        "→ battery → owner's gate). Nothing was written to the registry.",
        "",
    ]
    for n in nominations:
        lines.append(f"- `{n['model_key']}` (via {n['source']})")
    return "\n".join(lines) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--registry", default="src/registry.json")
    ap.add_argument("--map-file", default=None)
    ap.add_argument("--map-url", default=None,
                    help="fetch the cost map (default: map source off "
                         "unless --map-file or --map-url given)")
    ap.add_argument("--feed-file", default=None)
    ap.add_argument("--feed-url", default=None)
    ap.add_argument("--provider", action="append", default=None,
                    help="limit nominations to these provider prefixes "
                         "(default: the registry's own providers)")
    ap.add_argument("--all-providers", action="store_true")
    ap.add_argument("--hf-file", default=None,
                    help="HF router catalog JSON (offline/tests)")
    ap.add_argument("--hf-url", default=None,
                    help="fetch the public HF router catalog (no "
                         "credential is sent)")
    ap.add_argument("--hf-tools-only", action="store_true",
                    help="HF source: only models with a tool-capable "
                         "live provider")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    registry = json.loads(Path(args.registry).read_text(encoding="utf-8"))
    known = registry_known_ids(registry)
    providers: Optional[set[str]] = None
    if not args.all_providers:
        providers = set(args.provider) if args.provider \
            else registry_providers(registry)

    nominations: list[dict[str, Any]] = []
    if args.map_file or args.map_url:
        if args.map_file:
            litellm = json.loads(
                Path(args.map_file).read_text(encoding="utf-8"))
        else:
            litellm = load_json_url(args.map_url or DEFAULT_MAP_URL)
        nominations += nominate_from_map(litellm, known, providers)
    if args.feed_file or args.feed_url:
        if args.feed_file:
            feed_xml = Path(args.feed_file).read_text(encoding="utf-8")
        else:
            req = urllib.request.Request(
                args.feed_url or DEFAULT_FEED_URL,
                headers={"User-Agent": "sovereign-discovery-tripwire"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                feed_xml = resp.read().decode("utf-8")
        nominations += nominate_from_feed(feed_xml, known, providers)

    if args.hf_file or args.hf_url:
        nominations += hf_catalog.nominate_from_hf(
            hf_catalog.load_hf_catalog(args.hf_file, args.hf_url),
            hf_catalog.known_model_keys(registry), args.hf_tools_only)

    report = render_report(nominations)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
    print(json.dumps(nominations, indent=2) if args.json else report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
