"""v0.10.7: Hugging Face router catalog as a source for the discovery and
price-drift tripwires. Report-only by construction: the registry's bytes
must be unchanged across CLI runs, and the fetch must never carry a
credential."""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hf = _load("hf_catalog")
price_check = _load("price_check")
discovery = _load("discovery_tripwire")


def prov(slug, i, o, status="live", ctx=262144, tools=True):
    return {"provider": slug, "status": status, "context_length": ctx,
            "pricing": {"input": i, "output": o}, "supports_tools": tools}


CATALOG = [
    {"id": "deepseek-ai/DeepSeek-V4-Flash", "providers": [
        prov("deepinfra", 0.09, 0.18), prov("fireworks-ai", 0.07, 0.14)]},
    {"id": "Qwen/Qwen3-Coder-480B-A35B-Instruct", "providers": [
        prov("deepinfra", 0.30, 1.00), prov("venice", 0.35, 1.50)]},
    {"id": "acme/New-Model-1", "architecture": {"input_modalities": ["text"]},
     "providers": [prov("zeta", 0.5, 1.0, tools=False),
                   prov("alpha", 0.2, 0.4, tools=True)]},
    {"id": "acme/Dead-Model", "providers": [prov("zeta", 1, 1, status="error")]},
    {"id": "acme/No-Tools", "providers": [prov("zeta", 0.1, 0.1, tools=False)]},
]

REGISTRY = {"models": [
    {"id": "huggingface/deepseek-v4-flash", "provider": "huggingface",
     "model": "deepseek-ai/DeepSeek-V4-Flash:deepinfra",
     "input_usd_per_million": 0.09, "output_usd_per_million": 0.18},
    {"id": "huggingface/qwen3-coder-480b", "provider": "huggingface",
     "model": "Qwen/Qwen3-Coder-480B-A35B-Instruct:deepinfra",
     "input_usd_per_million": 0.40, "output_usd_per_million": 1.00},
    {"id": "huggingface/unpinned", "provider": "huggingface",
     "model": "deepseek-ai/DeepSeek-V4-Flash",
     "input_usd_per_million": 0.09, "output_usd_per_million": 0.18},
    {"id": "huggingface/dead-pin", "provider": "huggingface",
     "model": "acme/Dead-Model:zeta",
     "input_usd_per_million": 1.0, "output_usd_per_million": 1.0},
    {"id": "deepinfra/qwen3-coder-480b", "provider": "deepinfra",
     "model": "Qwen/Qwen3-Coder-480B-A35B-Instruct",
     "input_usd_per_million": 0.30, "output_usd_per_million": 1.00},
    {"id": "groq/not-on-hf", "provider": "groq", "model": "x/y",
     "input_usd_per_million": 1, "output_usd_per_million": 1},
]}


def test_split_route():
    assert hf.split_route("a/B:deepinfra") == ("a/B", "deepinfra")
    assert hf.split_route("a/B:fastest") == ("a/B", None)
    assert hf.split_route("a/B") == ("a/B", None)


def test_cheapest_live_ignores_non_live():
    m = {"providers": [prov("a", 1, 1, status="error"), prov("b", 2, 2), prov("c", 1, 3)]}
    assert hf.cheapest_live(m)["provider"] == "b"  # 4 vs 4 -> first min is b
    assert hf.cheapest_live({"providers": []}) is None


def test_price_drift_rows():
    s = hf.run_hf_check(REGISTRY, CATALOG, 0.01)
    by = {r["id"]: r for r in s["rows"]}
    assert by["huggingface/deepseek-v4-flash"]["drift"] is False
    assert by["huggingface/qwen3-coder-480b"]["drift"] is True
    assert by["huggingface/qwen3-coder-480b"]["fields"]["input_usd_per_million"]["hf"] == 0.30
    assert "huggingface/unpinned" not in by          # no pinned backend -> not compared
    assert "groq/not-on-hf" not in by
    assert by["deepinfra/qwen3-coder-480b"]["drift"] is False  # direct provider slug matched
    assert by["huggingface/dead-pin"]["not_live"] is True
    assert [r["id"] for r in s["drifted"]] == ["huggingface/qwen3-coder-480b"]


def test_nominations_exclude_known_dead_and_filter_tools():
    known = hf.known_model_keys(REGISTRY)
    noms = hf.nominate_from_hf(CATALOG, known)
    keys = [n["model_key"] for n in noms]
    assert "deepseek-ai/DeepSeek-V4-Flash" not in keys      # pinned route counts as known
    assert "Qwen/Qwen3-Coder-480B-A35B-Instruct" not in keys
    assert "acme/Dead-Model" not in keys                    # known via the dead-pin entry
    new = next(n for n in noms if n["model_key"] == "acme/New-Model-1")
    assert new["cheapest_provider"] == "alpha"
    assert new["input_usd_per_million"] == 0.2 and new["supports_tools"] is True
    tools_only = [n["model_key"] for n in hf.nominate_from_hf(CATALOG, known, only_tools=True)]
    assert "acme/No-Tools" not in tools_only and "acme/New-Model-1" in tools_only


def test_fetch_sends_no_credential(monkeypatch):
    seen = {}

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps({"data": CATALOG}).encode()

    def fake_urlopen(req, timeout=0):
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        seen["url"] = req.full_url
        return Resp()

    monkeypatch.setenv("HF_TOKEN", "hf_should_never_be_sent")
    monkeypatch.setattr(hf.urllib.request, "urlopen", fake_urlopen)
    out = hf.load_hf_catalog(url=hf.DEFAULT_HF_URL)
    assert len(out) == len(CATALOG)
    assert "authorization" not in seen["headers"]
    assert not any("hf_should_never" in str(v) for v in seen["headers"].values())
    assert seen["url"] == "https://router.huggingface.co/v1/models"


def test_cli_is_report_only_and_registry_bytes_unchanged(tmp_path, capsys):
    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps(REGISTRY), encoding="utf-8")
    cat = tmp_path / "hf.json"
    cat.write_text(json.dumps({"data": CATALOG}), encoding="utf-8")
    mp = tmp_path / "map.json"
    mp.write_text("{}", encoding="utf-8")
    before = reg.read_bytes()
    rc = price_check.main(["--registry", str(reg), "--map-file", str(mp),
                           "--hf-file", str(cat), "--json"])
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert [r["id"] for r in summary["hf"]["drifted"]] == ["huggingface/qwen3-coder-480b"]
    assert price_check.main(["--registry", str(reg), "--map-file", str(mp),
                             "--hf-file", str(cat), "--strict"]) == 1
    capsys.readouterr()
    rc = discovery.main(["--registry", str(reg), "--hf-file", str(cat), "--json"])
    assert rc == 0
    noms = json.loads(capsys.readouterr().out)
    assert any(n["source"] == "hf-router-catalog" for n in noms)
    assert reg.read_bytes() == before


def test_live_registry_cross_check_runs_on_fixture(tmp_path):
    reg = json.loads((ROOT / "src" / "registry.json").read_text(encoding="utf-8"))
    cat = [{"id": "Qwen/Qwen3-Coder-480B-A35B-Instruct",
            "providers": [prov("deepinfra", 0.30, 1.00)]}]
    s = hf.run_hf_check(reg, cat, 0.01)
    ids = {r["id"] for r in s["rows"]}
    assert "huggingface/qwen3-coder-480b" in ids
    assert not any(r["drift"] for r in s["rows"])
