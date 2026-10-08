"""The four specialised investigation agents. Each one: build STOIC prompt -> call model -> validate
schema (with one repair attempt) -> grounding / hallucination check -> return a traceable result."""
from __future__ import annotations

import time
from typing import Any

from pydantic import ValidationError

from . import config, guardrails, llm, prompts
from .schemas import AGENT_SCHEMAS


def run_agent(agent: str, model_cfg: dict, pack: dict, previous: dict[str, dict], pii_values: list[str]) -> dict[str, Any]:
    schema = AGENT_SCHEMAS[agent]
    system = prompts.system_prompt(agent)
    user = prompts.user_prompt(agent, pack, previous)

    # Data guardrail: refuse to send a prompt that still contains raw PII.
    leaked = guardrails.pii_leak_check(system + user, pii_values)
    if leaked:
        user = guardrails.redact_pii(user, leaked)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]

    result: dict[str, Any] = {
        "agent": agent, "model": model_cfg["label"], "prompt_version": prompts.PROMPT_VERSION, "ok": False,
        "output": None, "attempts": 0, "latency_ms": 0, "valid_first_try": False, "error": None,
        "raw": None, "pii_leak_blocked": bool(leaked), "rate_limit_wait_s": 0.0, "usage": {},
        "prompt_chars": len(system) + len(user),
    }
    started = time.perf_counter()
    for attempt in range(1 + config.LLM_MAX_RETRIES):
        result["attempts"] = attempt + 1
        try:
            resp = llm.chat(model_cfg, messages, mock_context={"agent": agent, "pack": pack, "previous": previous})
        except llm.LLMError as e:
            result["error"] = f"model_call_failed: {e}"
            break
        result["latency_ms"] += resp["latency_ms"]
        result["rate_limit_wait_s"] += resp.get("rate_limit_wait_s", 0)
        result["usage"] = resp.get("usage", {})
        result["raw"] = resp["content"][:4000]
        result["cached"] = bool(resp.get("cached"))
        try:
            data = llm.parse_json(resp["content"])
            parsed = schema.model_validate(data).model_dump()
        except (ValueError, ValidationError) as e:
            err = str(e).split("\n For further information")[0][:600]
            result["error"] = f"schema_invalid: {err}"
            messages = messages + [{"role": "assistant", "content": resp["content"][:4000]},
                                   {"role": "user", "content": prompts.REPAIR_PROMPT.format(error=err)}]
            continue
        result.update(ok=True, output=parsed, error=None, valid_first_try=(attempt == 0))
        break
    result["wall_ms"] = int((time.perf_counter() - started) * 1000)

    if result["ok"]:
        text = guardrails.output_text(result["output"])
        result["grounding"] = guardrails.grounding_check(text, guardrails.collect_ids(result["output"]), pack)
    else:
        result["grounding"] = {"invalid_citations": [], "unsupported_claims": [], "grounded": False}
    return result
