"""Guardrails: data, model and action controls applied around every agent.

Data guardrails   - PII redaction / pseudonymisation, PII-leak check on every prompt.
Model guardrails  - prompt-injection screening of untrusted text, schema validation, grounding
                    (citation + unsupported-claim) checks, rules-based risk floor.
Action guardrails - routing policy (A/H/E/X), confidence thresholds, role-based approval,
                    allow-list of actions, QA sampling of autonomous decisions.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from . import config

# ----------------------------------------------------------------------------- data guardrails
_PII_PATTERNS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[EMAIL]"),
    (re.compile(r"(?:\+91[\s-]?)?\b[6-9]\d{9}\b"), "[PHONE]"),
    (re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"), "[PAN]"),
    (re.compile(r"\b\d{4}\s\d{4}\s\d{4}\b"), "[AADHAAR]"),
    (re.compile(r"\b\d{9,18}\b"), lambda m: "XXXX" + m.group(0)[-4:]),  # account / card numbers
]


def redact_pii(text: str, extra_values: list[str] | tuple = ()) -> str:
    if not text:
        return text
    out = text
    for value in sorted({v for v in extra_values if v and len(v) >= 4}, key=len, reverse=True):
        out = re.sub(re.escape(value), "[NAME]" if not any(ch.isdigit() for ch in value) else "[REDACTED]",
                     out, flags=re.IGNORECASE)
    for pattern, repl in _PII_PATTERNS:
        out = pattern.sub(repl, out)
    return out


def pii_leak_check(prompt: str, pii_values: list[str]) -> list[str]:
    """Returns which raw PII values (if any) still appear in a prompt. Must be empty before a model call."""
    low = prompt.lower()
    return [v for v in pii_values if v and len(v) >= 4 and v.lower() in low]


# ----------------------------------------------------------------------------- model guardrails
_INJECTION_PATTERNS = [
    r"ignore (all |any )?(previous|prior|above|earlier) (instructions|prompts|rules)",
    r"disregard (the |all )?(previous|above|system)",
    r"(system|developer) (note|prompt|message|instruction)s? (to|for) (the )?(ai|assistant|model|reviewer)",
    r"\byou are now\b",
    r"\bact as\b",
    r"classify (this|the) (transaction|alert|case) as (legit|legitimate|genuine|safe)",
    r"risk score (of )?0\b",
    r"auto[- ]?close",
    r"(approve|release) (this|the) (transaction|payment) (immediately|now)",
    r"do not (flag|escalate|report)",
    r"\bjailbreak\b",
    r"</?(system|assistant|instructions)>",
]
_INJECTION_RE = [re.compile(p, re.IGNORECASE) for p in _INJECTION_PATTERNS]


def screen_untrusted_text(text: str) -> dict[str, Any]:
    """Prompt-injection screen for customer/merchant-entered text. Suspicious sentences are removed."""
    if not text:
        return {"sanitised": "", "injection_detected": False, "patterns": []}
    found: list[str] = []
    kept: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        hits = [p.pattern for p in _INJECTION_RE if p.search(sentence)]
        if hits:
            found.extend(hits)
            kept.append("[REMOVED BY GUARDRAIL: text addressed to the AI system]")
        else:
            kept.append(sentence)
    return {"sanitised": redact_pii(" ".join(kept)), "injection_detected": bool(found), "patterns": found}


# Claims a model might make, and the signal that must be true for the claim to be grounded.
_CLAIMS: list[tuple[str, str, Any]] = [
    ("sim_swap", r"sim[\s-]?swap|sim (re-?issue|change)", lambda s: s["sim_swap_72h"]),
    ("watchlist", r"watch[\s-]?list|blacklist|known mule account", lambda s: s["beneficiary_watchlisted"]),
    ("emulator", r"emulator|rooted|jail[\s-]?broken", lambda s: s["device_compromised"]),
    ("new_device", r"new(ly)?[\s-](registered |unrecognised |unknown |unfamiliar )?device|unrecognised device|unknown device",
     lambda s: s["device_new"] or not s["device_trusted"]),
    ("new_beneficiary", r"new(ly)?[\s-](added |registered )?(beneficiary|payee)", lambda s: s["beneficiary_new"]),
    ("password_reset", r"password[\s/]*(or mpin )?reset|credential reset|mpin reset", lambda s: s["password_reset_24h"]),
    ("otp_failure", r"(failed|wrong|incorrect) otp|otp (failure|attempt)", lambda s: s["otp_failures_1h"] > 0),
    ("foreign", r"\b(foreign|international|overseas|cross[\s-]border)\b",
     lambda s: s["foreign"] or s["impossible_travel"]),
    ("velocity", r"rapid succession|multiple transactions in (a )?short|high velocity|burst of", lambda s: s["velocity_10m"] >= 3),
    ("balance_drain", r"(drain|empt)(s|ies|ied|ying|ing)? (the )?(account|balance)|entire balance|(9\d|8\d)% of (the )?(available )?balance",
     lambda s: s["drain_pct"] >= 50),
    ("mule", r"\bmule\b", lambda s: s["beneficiary_watchlisted"] or s["credits_48h"] >= 5),
    ("card_testing", r"card[\s-]testing", lambda s: s["small_cnp_merchants_15m"] >= 2),
    ("elderly", r"\b(elderly|senior citizen|aged customer|pensioner)\b", lambda s: s["age"] >= 60),
    ("impossible_travel", r"impossible travel", lambda s: s["impossible_travel"]),
]
_NEGATION = re.compile(r"\b(no|not|without|absence of|none|never|nor|neither|zero|isn't|wasn't|aren't)\b[^.;:]{0,40}$",
                       re.IGNORECASE)


_NEG_SUFFIX = re.compile(r"^[\w\s]{0,15}?[:=]?\s*(0|none|nil|no|not (present|detected|found|observed))\b", re.IGNORECASE)


def grounding_check(text: str, cited_ids: list[str], pack: dict) -> dict[str, Any]:
    """Hallucination guard: cited evidence IDs must exist, and risk claims must be supported by data."""
    valid_ids = {f["id"] for f in pack["facts"]}
    invalid = sorted({c for c in cited_ids if c not in valid_ids})
    unsupported: list[str] = []
    signals = pack["signals"]
    for name, pattern, supported in _CLAIMS:
        for m in re.finditer(pattern, text or "", re.IGNORECASE):
            prefix = (text or "")[max(0, m.start() - 60):m.start()]
            suffix = (text or "")[m.end():m.end() + 25]
            if _NEGATION.search(prefix) or _NEG_SUFFIX.search(suffix):
                continue
            if not supported(signals):
                unsupported.append(name)
            break
    return {"invalid_citations": invalid, "unsupported_claims": sorted(set(unsupported)),
            "grounded": not invalid and not unsupported}


def output_text(data: dict) -> str:
    """Flattens an agent output to text for grounding checks (keys excluded)."""
    parts: list[str] = []

    def walk(v):
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, dict):
            for k, x in v.items():
                if k not in ("evidence_ids", "severity", "risk_band", "decision", "fraud_typology", "policy_flags"):
                    walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(data)
    return " | ".join(parts)


def collect_ids(data: Any) -> list[str]:
    ids: list[str] = []
    if isinstance(data, dict):
        for k, v in data.items():
            if k == "evidence_ids" and isinstance(v, list):
                ids.extend(str(x) for x in v)
            else:
                ids.extend(collect_ids(v))
    elif isinstance(data, list):
        for x in data:
            ids.extend(collect_ids(x))
    return ids


# ----------------------------------------------------------------------------- action guardrails
ROLES = {
    "ANALYST": "Fraud Analyst (L1)",
    "SENIOR": "Senior Fraud Investigator (L2)",
    "AUDITOR": "Internal Auditor (read-only)",
}

ACTIONS = {
    "auto_close_false_positive": {"label": "Close alert as false positive", "autonomous": True, "roles": ["SYSTEM"], "reversible": True},
    "notify_senior_investigator": {"label": "Notify L2 senior investigator", "autonomous": True, "roles": ["SYSTEM"], "reversible": True},
    "release_transaction": {"label": "Release / allow transaction", "autonomous": False, "roles": ["ANALYST", "SENIOR"], "reversible": False},
    "send_verification_request": {"label": "Step-up verification via registered channel", "autonomous": False, "roles": ["ANALYST", "SENIOR"], "reversible": True},
    "temporary_hold": {"label": "Temporary hold on transaction (max 24h)", "autonomous": False, "roles": ["ANALYST", "SENIOR"], "reversible": True},
    "block_card_or_channel": {"label": "Block card / digital channel", "autonomous": False, "roles": ["SENIOR"], "reversible": False},
    "freeze_debits": {"label": "Freeze account debits", "autonomous": False, "roles": ["SENIOR"], "reversible": False},
    "draft_str_for_compliance": {"label": "Draft Suspicious Transaction Report for compliance review", "autonomous": False, "roles": ["SENIOR"], "reversible": True},
    "advise_cybercrime_1930": {"label": "Advise customer to report on 1930 / cybercrime.gov.in", "autonomous": False, "roles": ["ANALYST", "SENIOR"], "reversible": True},
}

DECISION_ACTIONS = {
    "PROCEED": ["release_transaction"],
    "VERIFY": ["send_verification_request"],
    "HOLD": ["temporary_hold", "send_verification_request"],
    "ESCALATE": ["temporary_hold", "block_card_or_channel", "draft_str_for_compliance", "advise_cybercrime_1930"],
}


def allowed_decisions(role: str, route: str) -> list[str]:
    if role == "SENIOR":
        return ["PROCEED", "VERIFY", "HOLD", "ESCALATE"]
    if role == "ANALYST" and route in ("H", "A", "X"):
        return ["PROCEED", "VERIFY", "HOLD", "ESCALATE"]  # ESCALATE by an analyst = refer to L2, no blocking actions
    return []


def actions_for(decision: str, role: str) -> tuple[list[str], list[str]]:
    """Returns (permitted, withheld) actions for a human decision under the given role."""
    permitted, withheld = [], []
    for a in DECISION_ACTIONS.get(decision, []):
        (permitted if role in ACTIONS[a]["roles"] else withheld).append(a)
    return permitted, withheld


def qa_sampled(case_id: str) -> bool:
    h = int(hashlib.sha256(case_id.encode()).hexdigest(), 16) % 1000
    return h < config.QA_SAMPLE_RATE * 1000


def decide_route(pack: dict, outputs: dict[str, dict | None], grounding: dict[str, dict]) -> dict[str, Any]:
    """Final, deterministic routing policy. Models recommend; this policy decides who acts.

    A = autonomous close (low risk only), H = human-in-the-loop review, E = escalation to L2, X = exception.
    """
    flags: list[str] = []
    interventions: list[str] = []
    failed = [a for a, o in outputs.items() if o is None]
    if failed:
        return {"route": "X", "final_risk": pack.get("rule_score"), "risk_band": None, "decision": None,
                "confidence": None, "flags": [f"agent_failure:{a}" for a in failed], "interventions": [],
                "reason": "One or more agents failed to return valid output - sent to exception queue for manual handling."}

    rp, rec = outputs["risk_policy"], outputs["recommendation"]
    llm_risk = int(rp["risk_score"])
    rule_score = int(pack["rule_score"])
    final_risk = max(llm_risk, rule_score)
    if rule_score > llm_risk:
        interventions.append(f"risk_floor_applied: model risk {llm_risk} raised to rules score {rule_score}")
    from .tools import band  # local import avoids a cycle
    final_band = band(final_risk)
    decision = rec["decision"]
    confidence = float(rec["confidence"])
    critical_rule = any(h["severity"] == "CRITICAL" for h in pack["rule_hits"])
    high_rule = any(h["severity"] in ("HIGH", "CRITICAL") for h in pack["rule_hits"])

    if pack.get("injection_detected"):
        flags.append("prompt_injection_detected_in_remarks")
    ungrounded = [a for a, g in grounding.items() if not g["grounded"]]
    if ungrounded:
        flags.append("grounding_issue:" + ",".join(ungrounded))
    if decision == "PROCEED" and final_risk >= 60:
        flags.append("recommendation_inconsistent_with_risk")
        interventions.append("auto_close_blocked: PROCEED recommended despite HIGH risk evidence")
    if outputs["transaction_analysis"]["anomaly_score"] >= 70 and decision == "PROCEED":
        flags.append("agent_disagreement")

    if decision == "ESCALATE" or final_risk >= config.ESCALATE_MIN_RISK or critical_rule:
        route = "E"
        reason = "Escalated to L2: " + ("model recommends escalation" if decision == "ESCALATE" else
                                        "critical rule hit" if critical_rule else f"risk {final_risk} >= {config.ESCALATE_MIN_RISK}")
    elif (decision == "PROCEED" and final_risk < config.AUTO_CLOSE_MAX_RISK and confidence >= config.AUTO_CLOSE_MIN_CONFIDENCE
          and not flags and not high_rule):
        route = "A"
        reason = (f"Auto-closed as false positive: risk {final_risk} < {config.AUTO_CLOSE_MAX_RISK}, "
                  f"confidence {confidence:.2f} >= {config.AUTO_CLOSE_MIN_CONFIDENCE}, no guardrail flags.")
    else:
        route = "H"
        why = []
        if decision != "PROCEED":
            why.append(f"recommendation is {decision}")
        if final_risk >= config.AUTO_CLOSE_MAX_RISK:
            why.append(f"risk {final_risk} above auto-close limit")
        if confidence < config.AUTO_CLOSE_MIN_CONFIDENCE:
            why.append(f"confidence {confidence:.2f} below {config.AUTO_CLOSE_MIN_CONFIDENCE}")
        if flags:
            why.append("guardrail flags: " + ", ".join(flags))
        reason = "Human review required - " + "; ".join(why or ["policy requires analyst decision"])
    return {"route": route, "final_risk": final_risk, "risk_band": final_band, "decision": decision,
            "confidence": confidence, "llm_risk": llm_risk, "rule_score": rule_score, "flags": flags,
            "interventions": interventions, "reason": reason}
