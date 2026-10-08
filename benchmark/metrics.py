"""Metric definitions for the open-model comparison. Every metric is computed from the stored case
records, so results are reproducible and auditable."""
from __future__ import annotations

import re
import statistics
from collections import Counter

DECISIONS = ["PROCEED", "VERIFY", "HOLD", "ESCALATE"]
FLAGGING = {"HOLD", "ESCALATE"}

ACTION_WORDS = {
    "PROCEED": r"close|release|allow|no action|false positive|approve",
    "VERIFY": r"verif|contact|call|confirm|step[- ]up|otp|authenticat",
    "HOLD": r"hold|pause|suspend|verif|call|confirm",
    "ESCALATE": r"escalat|l2|senior|block|freeze|str|report|hold",
}

# Weights for the composite score (documented in the report; change only with justification).
WEIGHTS = {"acceptable_accuracy": 0.25, "route_accuracy": 0.15, "escalation_recall": 0.15, "fp_score": 0.10,
           "grounding_score": 0.10, "explanation_norm": 0.10, "schema_final": 0.10, "latency_score": 0.05}


def explanation_quality(record: dict, gt: dict) -> float | None:
    """0-5 rubric: citations, key-factor recall (2 pts), no hallucination, action consistent with decision."""
    rec = record.get("recommendation") or {}
    if not rec:
        return None
    text = " ".join([rec.get("rationale", ""), " ".join(rec.get("recommended_actions", [])),
                     (record.get("risk_policy") or {}).get("summary", ""),
                     " ".join(a.get("factor", "") for a in (record.get("transaction_analysis") or {}).get("anomalies", [])),
                     " ".join(a.get("factor", "") for a in (record.get("transaction_analysis") or {}).get("mitigating_factors", [])),
                     (record.get("customer_behaviour") or {}).get("summary", ""),
                     " ".join(sum(((record.get("customer_behaviour") or {}).get(k, []) for k in
                                   ("account_takeover_indicators", "social_engineering_indicators", "mule_indicators")), []))]).lower()
    score = 0.0
    valid_ids = set(record.get("valid_fact_ids", []))
    cited = [i for i in rec.get("evidence_ids", []) if i in valid_ids]
    if len(set(cited)) >= 2:
        score += 1
    kf = gt.get("key_factors", [])
    if kf:
        recall = sum(1 for pattern in kf if re.search(pattern, text)) / len(kf)
        score += 1 if recall >= 0.5 else 0
        score += 1 if recall >= 0.8 else 0
    else:
        score += 2
    if record.get("case_grounded"):
        score += 1
    if re.search(ACTION_WORDS.get(rec.get("decision", ""), "$^"), " ".join(rec.get("recommended_actions", [])).lower()):
        score += 1
    return score


def macro_f1(y_true: list[str], y_pred: list[str]) -> float:
    f1s = []
    for c in DECISIONS:
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == c and p == c)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != c and p == c)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == c and p != c)
        if tp + fp + fn == 0:
            continue
        prec = tp / (tp + fp) if tp + fp else 0
        rec = tp / (tp + fn) if tp + fn else 0
        f1s.append(2 * prec * rec / (prec + rec) if prec + rec else 0)
    return round(statistics.mean(f1s), 3) if f1s else 0.0


def _ratio(num: float, den: float) -> float | None:
    return round(num / den, 3) if den else None


def summarise(model: str, records: list[dict], gt_cases: dict) -> dict:
    """records: one per (case, repeat) for this model."""
    dec_recs = [r for r in records if gt_cases[r["alert_id"]]["expected_decision"]]
    y_true = [gt_cases[r["alert_id"]]["expected_decision"] for r in dec_recs]
    y_pred = [r["decision"] or "NONE" for r in dec_recs]
    correct = sum(1 for t, p in zip(y_true, y_pred) if t == p)
    acceptable = sum(1 for r in dec_recs if r["decision"] in gt_cases[r["alert_id"]]["acceptable_decisions"])
    route_ok = sum(1 for r in records if r["route"] in gt_cases[r["alert_id"]]["acceptable_routes"])
    route_strict = sum(1 for r in records if r["route"] == gt_cases[r["alert_id"]]["expected_route"])
    exp_e = [r for r in records if gt_cases[r["alert_id"]]["expected_route"] == "E"]
    routed_e = [r for r in records if r["route"] == "E"]
    legit = [r for r in records if gt_cases[r["alert_id"]]["is_fraud"] is False]
    fraud = [r for r in records if gt_cases[r["alert_id"]]["is_fraud"] is True]
    fp = sum(1 for r in legit if r["decision"] in FLAGGING or r["route"] == "E")
    caught = sum(1 for r in fraud if r["decision"] in FLAGGING)
    agent_calls = [a for r in records for a in r["agents"].values()]
    valid_calls = [a for a in agent_calls if a["ok"]]
    halluc_calls = [a for a in valid_calls if not a["grounded"]]
    expl = [x for x in (explanation_quality(r, gt_cases[r["alert_id"]]) for r in dec_recs) if x is not None]
    completed = sum(1 for r in records if r["completed"])
    lat = [r["latency_s"] for r in records if r["latency_s"]]  # data-exception case makes no model calls
    per_agent = {}
    for a in ("transaction_analysis", "customer_behaviour", "risk_policy", "recommendation"):
        vals = [r["agents"][a]["latency_ms"] / 1000 for r in records if a in r["agents"] and r["agents"][a]["ok"]]
        per_agent[a] = round(statistics.mean(vals), 2) if vals else None
    tokens = [r["tokens"] for r in records if r.get("tokens")]
    floor = sum(1 for r in records if any("risk_floor" in i for i in r.get("interventions", [])))
    blocked = sum(1 for r in records if any("auto_close_blocked" in i for i in r.get("interventions", [])))
    saves = sum(1 for r in dec_recs if r["decision"] not in gt_cases[r["alert_id"]]["acceptable_decisions"]
                and r["route"] in gt_cases[r["alert_id"]]["acceptable_routes"])

    out = {
        "model": model,
        "runs": len(records),
        "decision_accuracy": _ratio(correct, len(dec_recs)),
        "acceptable_accuracy": _ratio(acceptable, len(dec_recs)),
        "macro_f1": macro_f1(y_true, y_pred),
        "route_accuracy": _ratio(route_ok, len(records)),
        "route_accuracy_strict": _ratio(route_strict, len(records)),
        "escalation_recall": _ratio(sum(1 for r in exp_e if r["route"] == "E"), len(exp_e)),
        "escalation_precision": _ratio(sum(1 for r in routed_e if gt_cases[r["alert_id"]]["expected_route"] == "E"), len(routed_e)),
        "false_positive_rate": _ratio(fp, len(legit)),
        "fraud_recall": _ratio(caught, len(fraud)),
        "missed_fraud": sum(1 for r in fraud if r["decision"] == "PROCEED"),
        "hallucination_rate": _ratio(len(halluc_calls), len(valid_calls)),
        "cases_with_hallucination": sum(1 for r in records if r["completed"] and not r["case_grounded"] and r["route"] != "X"),
        "explanation_quality": round(statistics.mean(expl), 2) if expl else None,
        "schema_first_try": _ratio(sum(1 for a in agent_calls if a["valid_first_try"]), len(agent_calls)),
        "schema_final": _ratio(len(valid_calls), len(agent_calls)),
        "task_completion": _ratio(completed, len(records)),
        "avg_latency_s": round(statistics.mean(lat), 2) if lat else None,
        "p95_latency_s": round(sorted(lat)[max(0, int(round(0.95 * len(lat))) - 1)], 2) if lat else None,
        "avg_tokens_per_case": int(statistics.mean(tokens)) if tokens else None,
        "agent_latency_s": per_agent,
        "risk_floor_interventions": floor,
        "auto_close_blocked": blocked,
        "guardrail_saves": saves,
        "confusion": {t: dict(Counter(p for tt, p in zip(y_true, y_pred) if tt == t)) for t in DECISIONS},
    }
    lat_score = min(1.0, 8.0 / out["avg_latency_s"]) if out["avg_latency_s"] else 0
    parts = {
        "acceptable_accuracy": out["acceptable_accuracy"] or 0,
        "route_accuracy": out["route_accuracy"] or 0,
        "escalation_recall": out["escalation_recall"] or 0,
        "fp_score": 1 - (out["false_positive_rate"] or 0),
        "grounding_score": 1 - (out["hallucination_rate"] or 0),
        "explanation_norm": (out["explanation_quality"] or 0) / 5,
        "schema_final": out["schema_final"] or 0,
        "latency_score": lat_score,
    }
    out["composite_score"] = round(100 * sum(WEIGHTS[k] * v for k, v in parts.items()), 1)
    return out
