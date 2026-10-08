"""Checks which models your API keys can actually reach, with one tiny request each.

  python -m app.check_models                 # every model whose key is set in .env
  python -m app.check_models gemma-4-31b     # specific labels
"""
from __future__ import annotations

import sys

from . import config, llm

PROBE = [
    {"role": "system", "content": "You are a health-check. Reply with JSON only."},
    {"role": "user", "content": 'Return exactly this JSON object: {"ok": true, "model_says": "ready"}'},
]


def _ollama_running() -> bool:
    import httpx
    try:
        return httpx.get(config.PROVIDERS["ollama"]["base_url"].replace("/v1", "/api/tags"), timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def _gemini_listing() -> None:
    """Asks Google AI Studio which models this key can call, so retired names are easy to spot."""
    import httpx
    key = llm._api_key("gemini")
    if not key:
        return
    try:
        r = httpx.get(f"{config.PROVIDERS['gemini']['base_url']}/models", params={"pageSize": 1000},
                      headers={"x-goog-api-key": key}, timeout=20)
        names = sorted(m["name"].split("/", 1)[-1] for m in r.json().get("models", [])
                       if "generateContent" in m.get("supportedGenerationMethods", []))
    except (httpx.HTTPError, ValueError, KeyError):
        return
    gemma = [n for n in names if n.startswith("gemma")]
    print("Google AI Studio offers these open-weight Gemma models to your key: " + (", ".join(gemma) or "none"))
    missing = [m["label"] for m in config.load_models() if m["provider"] == "gemini" and m["model"] not in names]
    if missing:
        print("Not offered any more (retired or renamed): " + ", ".join(missing))
    print()


def main(labels: list[str]) -> int:
    config.LLM_CACHE_ENABLED = False  # always ask the provider for real; never report a cached answer as "OK"
    _gemini_listing()
    models = [m for m in config.load_models() if m["provider"] != "mock"]
    if labels:
        models = [m for m in models if m["label"] in labels]
    ok_count = 0
    print(f"{'model':28s} {'provider':11s} result")
    print("-" * 78)
    for m in models:
        ready, note = llm.provider_ready(m)
        if ready and m["provider"] == "ollama" and not labels and not _ollama_running():
            continue
        if not ready:
            if labels:
                print(f"{m['label']:28s} {m['provider']:11s} SKIPPED - {note}")
            continue
        try:
            r = llm.chat(m, PROBE)
            parsed = llm.parse_json(r["content"])
            extra = f" (dropped: {', '.join(r['dropped_params'])})" if r.get("dropped_params") else ""
            print(f"{m['label']:28s} {m['provider']:11s} OK  {r['latency_ms']} ms  -> {parsed}{extra}")
            ok_count += 1
        except (llm.LLMError, ValueError) as e:
            print(f"{m['label']:28s} {m['provider']:11s} FAILED - {str(e)[:160]}")
    if not any(llm.provider_ready(m)[0] for m in models):
        print("No API keys found. Put GEMINI_API_KEY (and optionally CEREBRAS_API_KEY) in .env - see README.")
    print(f"\n{ok_count} model(s) reachable. Use their labels with: run_benchmark.bat --models <label> <label> <label>")
    return 0 if ok_count else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
