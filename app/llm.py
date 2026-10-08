"""Provider-agnostic LLM client plus an offline mock.

Two wire formats cover every provider:
* OpenAI-compatible chat completions: Cerebras, Groq, Mistral, NVIDIA NIM, OpenRouter, Together, Ollama (local).
* Google Gemini API (native generateContent): Google AI Studio key, serving the open-weight Gemma models
  (and Gemini, used only as an optional closed-model reference).
Every model receives byte-identical prompts. The 'mock' provider is a deterministic rule-based stand-in used
for offline demos and automated tests; it is excluded from the model benchmark.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import httpx

from . import config


class LLMError(Exception):
    pass


def _api_key(provider: str) -> str | None:
    env = config.PROVIDERS[provider]["api_key_env"]
    if not env:
        return None
    return os.getenv(env) or next((os.getenv(a) for a in config.PROVIDERS[provider].get("alt_env", []) if os.getenv(a)), None)


def provider_ready(model_cfg: dict) -> tuple[bool, str]:
    p = model_cfg["provider"]
    if p == "mock":
        return True, "offline mock"
    if p not in config.PROVIDERS:
        return False, f"unknown provider '{p}'"
    env = config.PROVIDERS[p]["api_key_env"]
    if env and not _api_key(p):
        return False, f"set {env} in .env"
    return True, config.PROVIDERS[p]["base_url"]


_DROPPED: dict[str, set[str]] = {}  # request features a model rejected once - not sent again

_RETRY_RES = [re.compile(r"try again in ([\d.]+)(ms|s|m)\b", re.IGNORECASE),
              re.compile(r'"retryDelay"\s*:\s*"([\d.]+)(s)"'),
              re.compile(r"retry (?:after|in) ([\d.]+) ?(ms|s|seconds?|m)\b", re.IGNORECASE)]


def _retry_after(resp: httpx.Response) -> float:
    if resp.headers.get("retry-after"):
        try:
            return float(resp.headers["retry-after"])
        except ValueError:
            pass
    for rx in _RETRY_RES:
        m = rx.search(resp.text)
        if m:
            v, unit = float(m.group(1)), m.group(2).lower()
            return v / 1000 if unit == "ms" else v * 60 if unit == "m" else v
    return 10.0


def _merge_system(messages: list[dict]) -> list[dict]:
    """For models that reject a system role: fold the system prompt into the first user message."""
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    rest = [dict(m) for m in messages if m["role"] != "system"]
    if system and rest:
        rest[0]["content"] = system + "\n\n---\n\n" + rest[0]["content"]
    return rest


def _rejects(text: str, feature: str) -> bool:
    t = text.lower()
    if feature == "system":
        return any(w in t for w in ("system", "developer instruction", "instruction is not enabled", "role"))
    if feature == "json":
        return any(w in t for w in ("json", "response_format", "responsemimetype", "mime"))
    return False


def _cache_path(model_cfg: dict, messages: list[dict]):
    import hashlib
    key = json.dumps({"p": model_cfg["provider"], "m": model_cfg["model"], "x": model_cfg.get("extra", {}),
                      "msgs": messages}, sort_keys=True, ensure_ascii=False)
    return config.LLM_CACHE_DIR / (hashlib.sha256(key.encode()).hexdigest()[:32] + ".json")


def chat(model_cfg: dict, messages: list[dict], *, mock_context: dict | None = None) -> dict[str, Any]:
    """Returns {'content', 'latency_ms', 'usage', 'rate_limit_wait_s', 'cached'}. Latency excludes rate-limit waits."""
    use_cache = config.LLM_CACHE_ENABLED and model_cfg["provider"] != "mock" and "#" not in model_cfg["label"]
    if use_cache:
        path = _cache_path(model_cfg, messages)
        if path.exists():
            try:
                hit = json.loads(path.read_text(encoding="utf-8"))
                return {**hit, "latency_ms": 0, "rate_limit_wait_s": 0, "cached": True}
            except (OSError, ValueError):
                pass
    resp = _chat(model_cfg, messages, mock_context=mock_context)
    if use_cache:
        try:
            config.LLM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            _cache_path(model_cfg, messages).write_text(json.dumps(
                {"content": resp["content"], "usage": resp.get("usage", {}), "original_latency_ms": resp["latency_ms"]}),
                encoding="utf-8")
        except OSError:
            pass
    return {**resp, "cached": False}


def _chat(model_cfg: dict, messages: list[dict], *, mock_context: dict | None = None) -> dict[str, Any]:
    if model_cfg["provider"] == "mock":
        t0 = time.perf_counter()
        content = json.dumps(mock_respond(mock_context["agent"], mock_context["pack"], mock_context["previous"]))
        return {"content": content, "latency_ms": int((time.perf_counter() - t0) * 1000) + 5,
                "usage": {}, "rate_limit_wait_s": 0}
    provider = model_cfg["provider"]
    if provider not in config.PROVIDERS:
        raise LLMError(f"Unknown provider '{provider}' in models.json")
    prov = config.PROVIDERS[provider]
    key = _api_key(provider)
    if prov["api_key_env"] and not key:
        raise LLMError(f"Missing API key: set {prov['api_key_env']} in .env")
    native_gemini = prov.get("format") == "gemini"
    timeout = config.LLM_TIMEOUT_S * (2 if provider == "ollama" else 1)
    known_bad = _DROPPED.setdefault(model_cfg["label"], set())
    extras = dict(model_cfg.get("extra", {}))
    waited = 0.0
    dropped: list[str] = []

    for attempt in range(10):
        msgs = _merge_system(messages) if "system" in known_bad else messages
        if native_gemini:
            url, headers, body = _gemini_request(prov, model_cfg, msgs, key, known_bad, extras)
        else:
            url = f"{prov['base_url']}/chat/completions"
            headers = {"Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if key else {})}
            body = {"model": model_cfg["model"], "messages": msgs, "temperature": 0,
                    "max_tokens": int(model_cfg.get("max_tokens", 1500)),
                    **({} if "json" in known_bad else {"response_format": {"type": "json_object"}}),
                    **{k: v for k, v in extras.items() if k not in known_bad}}
        t0 = time.perf_counter()
        try:
            resp = httpx.post(url, json=body, headers=headers, timeout=timeout)
        except httpx.HTTPError as e:
            if attempt < 2:
                time.sleep(2)
                continue
            raise LLMError(f"Connection error to {provider}: {e}") from e
        latency_ms = int((time.perf_counter() - t0) * 1000)
        if resp.status_code == 429:
            wait = min(_retry_after(resp) + 0.5, 90)
            waited += wait
            time.sleep(wait)
            continue
        if resp.status_code in (401, 403):
            raise LLMError(f"HTTP {resp.status_code} from {provider}: API key rejected or model not enabled for this key. "
                           f"{resp.text[:200]}")
        if resp.status_code in (400, 422):
            # Drop one unsupported feature at a time (JSON mode, system role, provider extras) and retry.
            norm = lambda x: re.sub(r"[^a-z]", "", x.lower())  # noqa: E731 - thinkingConfig == thinking_config
            text_n = norm(resp.text)

            def _names(v, k):
                return [k] + ([kk for kk in v] if isinstance(v, dict) else [])
            named = [f for f in extras if f not in known_bad and any(norm(n) in text_n for n in _names(extras[f], f))]
            named += [f for f in ("json", "system") if f not in known_bad and _rejects(resp.text, f)]
            fallback = [f for f in (*extras.keys(), "json", "system") if f not in known_bad]
            feat = (named or fallback or [None])[0]
            if feat is None:
                raise LLMError(f"HTTP {resp.status_code} from {provider}: {resp.text[:300]}")
            known_bad.add(feat)
            dropped.append(feat)
            continue
        if resp.status_code == 404:
            raise LLMError(f"model '{model_cfg['model']}' is not available from {provider} (retired or wrong name). "
                           "Choose another model; launcher option 2 shows which models your key can reach.")
        if resp.status_code >= 500 and attempt < 4:
            time.sleep(3 + 2 * attempt)
            continue
        if resp.status_code != 200:
            raise LLMError(f"HTTP {resp.status_code} from {provider}: {resp.text[:300]}")
        data = resp.json()
        content, usage = _gemini_parse(data) if native_gemini else _openai_parse(data)
        if not content.strip():
            raise LLMError("Model returned an empty reply (output limit reached, possibly by hidden reasoning) - "
                           "raise max_tokens for this model in models.json")
        return {"content": content, "latency_ms": latency_ms, "usage": usage,
                "rate_limit_wait_s": round(waited, 1), "dropped_params": dropped}
    raise LLMError("Gave up after repeated rate-limit / server errors")


def _openai_parse(data: dict) -> tuple[str, dict]:
    msg = (data.get("choices") or [{}])[0].get("message") or {}
    return msg.get("content") or "", data.get("usage", {})


def _gemini_request(prov: dict, model_cfg: dict, msgs: list[dict], key: str, known_bad: set, extras: dict):
    system = "\n\n".join(m["content"] for m in msgs if m["role"] == "system")
    contents = [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
                for m in msgs if m["role"] != "system"]
    gen = {"temperature": 0, "maxOutputTokens": int(model_cfg.get("max_tokens", 2048))}
    if "json" not in known_bad:
        gen["responseMimeType"] = "application/json"
    for k, v in extras.items():
        if k not in known_bad:
            gen[k] = v
    body: dict[str, Any] = {"contents": contents, "generationConfig": gen}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    url = f"{prov['base_url']}/models/{model_cfg['model']}:generateContent"
    return url, {"Content-Type": "application/json", "x-goog-api-key": key}, body


def _gemini_parse(data: dict) -> tuple[str, dict]:
    cands = data.get("candidates") or []
    if not cands:
        raise LLMError(f"No candidates returned (prompt feedback: {json.dumps(data.get('promptFeedback', {}))[:200]})")
    parts = (cands[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    um = data.get("usageMetadata", {})
    usage = {"prompt_tokens": um.get("promptTokenCount"), "completion_tokens": um.get("candidatesTokenCount"),
             "total_tokens": um.get("totalTokenCount")}
    return text, usage


def parse_json(content: str) -> dict:
    """Extracts the first JSON object from a model reply (tolerates <think> blocks and code fences)."""
    text = re.sub(r"<think>.*?</think>", "", content or "", flags=re.DOTALL).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                esc = (ch == "\\") and not esc
                if ch == '"' and not esc:
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    raise ValueError("No JSON object found in model output")


# ------------------------------------------------------------------------------------------- mock
def _fid(pack: dict, source: str) -> list[str]:
    return [f["id"] for f in pack["facts"] if f["source"] == source][:1]


def mock_respond(agent: str, pack: dict, previous: dict) -> dict:
    """Deterministic, rules-driven stand-in for an LLM. Good enough to exercise the whole workflow offline."""
    s = pack["signals"]
    hits = {h["rule_id"]: h for h in pack["rule_hits"]}
    rs = pack["rule_score"]
    ato = []
    if s["sim_swap_72h"]:
        ato.append("SIM swap in last 72h")
    if s["password_reset_24h"]:
        ato.append("Password reset in last 24h")
    if s["device_compromised"]:
        ato.append("Emulator / rooted device")
    if s["device_new"] and not s["device_trusted"]:
        ato.append("Transaction from a new, untrusted device")
    if s["otp_failures_1h"] >= 3:
        ato.append(f"{s['otp_failures_1h']} failed OTP attempts")
    se = []
    if s["remarks_scam_keywords"]:
        se.append("Remarks match a known scam narrative")
    if s["age"] >= 60 and s["remarks_scam_keywords"]:
        se.append("Customer aged 60+ is a high-risk scam target")
    if pack["injection_detected"]:
        se.append("Remarks contained instructions aimed at the AI reviewer")
    mule = []
    if s["beneficiary_watchlisted"]:
        mule.append("Beneficiary on mule watchlist")
    if s["credits_48h"] >= 10:
        mule.append(f"{s['credits_48h']} inbound credits from {s['distinct_senders_48h']} senders then outbound transfer")

    mitig = []
    if s["beneficiary_prior_payments"]:
        mitig.append({"factor": f"Known beneficiary with {s['beneficiary_prior_payments']} earlier payments",
                      "evidence_ids": _fid(pack, "beneficiary")})
    if s["prior_false_positives"]:
        mitig.append({"factor": "Similar alert previously confirmed as a false positive", "evidence_ids": _fid(pack, "history")})
    if s["channel"] == "CARD_POS":
        mitig.append({"factor": "Card-present chip and PIN transaction", "evidence_ids": _fid(pack, "transaction")})
    if s["device_trusted"] and not s["device_new"]:
        mitig.append({"factor": "Long-standing trusted device", "evidence_ids": _fid(pack, "device")})
    if s["channel"] == "UPI" and s["velocity_10m"] >= 5 and not ato:
        mitig.append({"factor": "Burst consists of routine bill payments", "evidence_ids": _fid(pack, "recent")})

    if agent == "transaction_analysis":
        sev = {"LOW": "LOW", "MEDIUM": "MEDIUM", "HIGH": "HIGH", "CRITICAL": "HIGH"}
        anomalies = [{"factor": h["evidence"], "severity": sev[h["severity"]], "evidence_ids": _fid(pack, "rules")}
                     for h in pack["rule_hits"]]
        score = max(0, min(100, rs + (10 if s["impossible_travel"] else 0) - 5 * len(mitig)))
        return {"anomaly_score": score, "anomalies": anomalies, "mitigating_factors": mitig,
                "summary": f"{len(anomalies)} rule-based anomalies and {len(mitig)} mitigating factors identified for "
                           f"this {s['channel']} transaction of Rs {s['amount']:,.0f} ({s['amount_ratio']}x baseline)."}
    if agent == "customer_behaviour":
        risk = min(100, 15 * len(ato) + 25 * len(se) + 35 * len(mule))
        return {"behaviour_risk": risk, "consistent_with_profile": not (ato or se or mule),
                "account_takeover_indicators": ato, "social_engineering_indicators": se, "mule_indicators": mule,
                "evidence_ids": _fid(pack, "customer") + _fid(pack, "device") + _fid(pack, "security"),
                "summary": ("No takeover, scam or mule indicators; activity fits the customer's profile."
                            if not (ato or se or mule) else
                            "Indicators found: " + "; ".join(ato + se + mule) + ".")}
    if agent == "risk_policy":
        cb = previous.get("customer_behaviour") or {}
        risk = max(rs, int(0.6 * rs + 0.5 * cb.get("behaviour_risk", 0)))
        if s["impossible_travel"]:
            risk = max(risk, 35)
        if not ato and not se and not mule and "R05" not in hits and len(mitig) >= 2:
            risk = max(0, risk - 5)
        risk = min(100, risk)
        b = "CRITICAL" if risk >= 80 else "HIGH" if risk >= 60 else "MEDIUM" if risk >= 30 else "LOW"
        typ = ("ACCOUNT_TAKEOVER" if len(ato) >= 2 else "MULE_ACCOUNT" if mule else
               "AUTHORISED_PUSH_PAYMENT_SCAM" if se and s["remarks_scam_keywords"] else
               "CARD_TESTING" if "R12" in hits else "NONE" if b == "LOW" else "UNCLEAR")
        return {"risk_score": risk, "risk_band": b, "fraud_typology": typ, "policy_flags": list(hits),
                "regulatory_notes": ["Record rationale for audit trail"],
                "evidence_ids": _fid(pack, "rules") + _fid(pack, "alert"),
                "summary": f"Combined risk {risk} ({b}); typology {typ}."}
    # recommendation
    rp = previous.get("risk_policy") or {}
    risk = rp.get("risk_score", rs)
    if risk >= 70 or "R05" in hits or "R11" in hits or "R12" in hits:
        decision, conf = "ESCALATE", 0.9
        actions = ["Refer to L2 investigator", "Place temporary hold", "Prepare STR draft for compliance"]
    elif s["age"] >= 60 and s["remarks_scam_keywords"]:
        decision, conf = "HOLD", 0.85
        actions = ["Hold payment", "Call customer on registered number"]
    elif risk >= 30:
        decision, conf = "VERIFY", 0.75
        actions = ["Step-up verification with customer via registered channel"]
    else:
        decision, conf = "PROCEED", 0.9
        actions = ["Close alert as false positive"]
    ids = _fid(pack, "transaction") + _fid(pack, "rules") + _fid(pack, "baseline")
    return {"decision": decision, "confidence": conf,
            "rationale": f"Combined risk is {risk} with {len(pack['rule_hits'])} rule hit(s) ({', '.join(hits) or 'none'}); "
                         f"see {', '.join(ids)}. Recommendation follows bank policy for this risk level.",
            "evidence_ids": ids, "recommended_actions": actions,
            "customer_message": None, "needs_human_review": decision != "PROCEED"}
