"""Case lifecycle. Each step is a separate function so that n8n can call them one by one over HTTP,
while `investigate()` runs the identical sequence in-process (Python fallback and benchmark)."""
from __future__ import annotations

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import audit, config, db, guardrails, tools
from .agents import run_agent
from .schemas import AGENT_ORDER, AGENT_TITLES

# Which earlier outputs each agent reads. Agents 1 and 2 only read the evidence, so they can run in parallel.
DEPENDS = {
    "transaction_analysis": [],
    "customer_behaviour": [],
    "risk_policy": ["transaction_analysis", "customer_behaviour"],
    "recommendation": ["transaction_analysis", "customer_behaviour", "risk_policy"],
}
_CASE_LOCK = threading.Lock()

ROUTE_STATUS = {"A": "AUTO_CLOSED", "H": "PENDING_REVIEW", "E": "ESCALATED", "X": "EXCEPTION"}
ROUTE_LABEL = {"A": "Autonomous", "H": "Human-in-the-Loop", "E": "Escalation", "X": "Exception"}


class CaseError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _now() -> str:
    return audit.now()


def get_case(conn, case_id: str) -> dict:
    c = db.row(conn, "SELECT * FROM cases WHERE case_id=?", (case_id,))
    if not c:
        raise CaseError(f"Case {case_id} not found", 404)
    for k, default in (("evidence_json", None), ("agents_json", {}), ("guardrails_json", {}), ("actions_json", [])):
        c[k.replace("_json", "")] = db.loads(c.pop(k), default)
    return c


def _save(conn, case_id: str, **fields) -> None:
    fields["updated_at"] = _now()
    for k in list(fields):
        if k in ("evidence", "agents", "guardrails", "actions"):
            fields[k + "_json"] = json.dumps(fields.pop(k), ensure_ascii=False, default=str)
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE cases SET {sets} WHERE case_id=?", (*fields.values(), case_id))


# ------------------------------------------------------------------------------------- steps
def start_case(conn, alert_id: str, model_label: str, orchestrator: str = "python") -> str:
    config.get_model(model_label)  # validates label
    if not db.row(conn, "SELECT 1 AS x FROM alerts WHERE alert_id=?", (alert_id,)):
        raise CaseError(f"Alert {alert_id} not found", 404)
    case_id = f"CASE-{alert_id.split('-')[1]}-{uuid.uuid4().hex[:6].upper()}"
    conn.execute("INSERT INTO cases (case_id, alert_id, model_label, orchestrator, status, agents_json, created_at, updated_at) "
                 "VALUES (?,?,?,?,?,?,?,?)", (case_id, alert_id, model_label, orchestrator, "NEW", "{}", _now(), _now()))
    conn.execute("UPDATE alerts SET status='IN_INVESTIGATION' WHERE alert_id=?", (alert_id,))
    audit.log(conn, case_id, f"orchestrator:{orchestrator}", "CASE_OPENED", {"alert_id": alert_id, "model": model_label})
    return case_id


def step_evidence(conn, case_id: str) -> dict:
    case = get_case(conn, case_id)
    try:
        pack = tools.build_evidence_pack(conn, case["alert_id"])
    except tools.DataUnavailable as e:
        _save(conn, case_id, status="EVIDENCE_FAILED", error=str(e))
        audit.log(conn, case_id, "tool:evidence", "EVIDENCE_FAILED", {"error": str(e)})
        return {"ok": False, "case_id": case_id, "error": str(e)}
    stored = {k: v for k, v in pack.items() if k != "pii_values"}
    _save(conn, case_id, status="EVIDENCE_READY", evidence=stored)
    audit.log(conn, case_id, "tool:evidence", "EVIDENCE_COLLECTED",
              {"facts": len(pack["facts"]), "rule_hits": [h["rule_id"] for h in pack["rule_hits"]],
               "rule_score": pack["rule_score"], "injection_detected": pack["injection_detected"]})
    if pack["injection_detected"]:
        audit.log(conn, case_id, "guardrail:injection_screen", "PROMPT_INJECTION_QUARANTINED",
                  {"patterns": pack["injection_patterns"]})
    return {"ok": True, "case_id": case_id, "facts": len(pack["facts"]), "rule_score": pack["rule_score"],
            "rule_band": pack["rule_band"], "rule_hits": [h["rule_id"] for h in pack["rule_hits"]],
            "injection_detected": pack["injection_detected"]}


def _pii_values(conn, case: dict) -> list[str]:
    alert = tools.get_alert(conn, case["alert_id"])
    cust = db.row(conn, "SELECT * FROM customers WHERE customer_id=?", (alert["customer_id"],)) or {}
    acct = db.row(conn, "SELECT account_number FROM accounts WHERE account_id=?", (alert["account_id"],)) or {}
    bens = db.rows(conn, "SELECT display_name, account_number FROM beneficiaries WHERE customer_id=?", (alert["customer_id"],))
    vals = [cust.get("full_name"), cust.get("phone"), cust.get("email"), cust.get("pan"), acct.get("account_number")]
    for b in bens:
        vals += [b["account_number"], b["display_name"].split(" (")[0]]
    return [v for v in vals if v]


def step_agent(conn, case_id: str, agent: str) -> dict:
    if agent not in AGENT_ORDER:
        raise CaseError(f"Unknown agent '{agent}'", 404)
    case = get_case(conn, case_id)
    if not case["evidence"]:
        raise CaseError("Evidence has not been collected for this case", 409)
    previous = {a: (case["agents"].get(a) or {}).get("output") for a in DEPENDS[agent]}
    missing = [a for a, out in previous.items() if out is None]
    if missing:
        raise CaseError(f"Earlier agent(s) {missing} have not produced valid output", 409)
    model_cfg = config.get_model(case["model_label"])
    pii = _pii_values(conn, case)
    conn.commit()  # release any read transaction before the (slow) model call
    res = run_agent(agent, model_cfg, case["evidence"], previous, pii)
    with _CASE_LOCK:  # agents 1 and 2 may finish at the same moment: merge, never overwrite
        fresh = get_case(conn, case_id)
        fresh["agents"][agent] = res
        failed = [a for a, r in fresh["agents"].items() if r and not r.get("ok")]
        _save(conn, case_id, status="AGENT_FAILED" if failed else "IN_ANALYSIS", agents=fresh["agents"],
              error=None if not failed else f"{failed[0]}: {fresh['agents'][failed[0]].get('error')}")
        audit.log(conn, case_id, f"agent:{agent}", "AGENT_COMPLETED" if res["ok"] else "AGENT_FAILED",
                  {"model": model_cfg["label"], "latency_ms": res["latency_ms"], "attempts": res["attempts"],
                   "valid_first_try": res["valid_first_try"], "grounding": res["grounding"], "error": res["error"],
                   "pii_leak_blocked": res["pii_leak_blocked"], "prompt_version": res["prompt_version"],
                   "cached": res.get("cached", False)})
        conn.commit()
    return {"ok": res["ok"], "case_id": case_id, "agent": agent, "title": AGENT_TITLES[agent], "output": res["output"],
            "latency_ms": res["latency_ms"], "attempts": res["attempts"], "error": res["error"],
            "grounding": res["grounding"]}


def step_route(conn, case_id: str) -> dict:
    case = get_case(conn, case_id)
    if case["status"] == "EVIDENCE_FAILED" or not case["evidence"]:
        g = {"route": "X", "final_risk": None, "risk_band": None, "decision": None, "confidence": None,
             "flags": ["data_unavailable"], "interventions": [],
             "reason": f"Exception: {case.get('error') or 'evidence unavailable'} - manual data retrieval required."}
    else:
        outputs = {a: (case["agents"].get(a) or {}).get("output") if (case["agents"].get(a) or {}).get("ok") else None
                   for a in AGENT_ORDER}
        grounding = {a: (case["agents"].get(a) or {}).get("grounding", {"grounded": True}) for a in AGENT_ORDER
                     if outputs.get(a) is not None}
        g = guardrails.decide_route(case["evidence"], outputs, grounding)
    _save(conn, case_id, guardrails=g)
    audit.log(conn, case_id, "guardrail:routing_policy", "ROUTE_DECIDED", g)
    return {"case_id": case_id, **g, "route_label": ROUTE_LABEL[g["route"]]}


def apply_route(conn, case_id: str, route: str | None = None) -> dict:
    case = get_case(conn, case_id)
    g = case["guardrails"] or {}
    route = route or g.get("route")
    if route not in ROUTE_STATUS:
        raise CaseError(f"Invalid route '{route}'")
    if g.get("route") and route != g["route"]:
        raise CaseError(f"Route {route} conflicts with policy decision {g['route']}", 409)
    actions: list[dict] = case["actions"] or []
    qa = 0
    if route == "A":
        actions.append({"action": "auto_close_false_positive", "by": "SYSTEM", "at": _now(), "status": "EXECUTED"})
        qa = int(guardrails.qa_sampled(case_id))
        conn.execute("UPDATE alerts SET status='CLOSED_FALSE_POSITIVE' WHERE alert_id=?", (case["alert_id"],))
        audit.log(conn, case_id, "SYSTEM", "AUTO_CLOSED", {"qa_sampled": bool(qa), "reason": g.get("reason")})
    elif route == "E":
        _notify(conn, case_id, "L2_QUEUE", "Senior Fraud Investigator on duty",
                f"Escalated case {case_id} (risk {g.get('final_risk')}, {g.get('decision')}). SLA: 15 minutes.")
        actions.append({"action": "notify_senior_investigator", "by": "SYSTEM", "at": _now(), "status": "EXECUTED"})
        conn.execute("UPDATE alerts SET status='ESCALATED' WHERE alert_id=?", (case["alert_id"],))
    elif route == "H":
        conn.execute("UPDATE alerts SET status='PENDING_REVIEW' WHERE alert_id=?", (case["alert_id"],))
    else:
        _notify(conn, case_id, "OPS_EXCEPTION_QUEUE", "Fraud operations supervisor",
                f"Case {case_id} could not be processed automatically: {case.get('error') or g.get('reason')}")
        conn.execute("UPDATE alerts SET status='EXCEPTION' WHERE alert_id=?", (case["alert_id"],))
    total = sum((a or {}).get("latency_ms", 0) for a in case["agents"].values())
    _save(conn, case_id, status=ROUTE_STATUS[route], route=route, risk_score=g.get("final_risk"),
          risk_band=g.get("risk_band"), decision=g.get("decision"), confidence=g.get("confidence"),
          actions=actions, qa_sampled=qa, total_latency_ms=total)
    audit.log(conn, case_id, "orchestrator", "ROUTE_APPLIED", {"route": route, "status": ROUTE_STATUS[route]})
    return summary(conn, case_id)


def _notify(conn, case_id: str, channel: str, recipient: str, message: str) -> None:
    conn.execute("INSERT INTO notifications (ts, case_id, channel, recipient, message) VALUES (?,?,?,?,?)",
                 (_now(), case_id, channel, recipient, message))
    audit.log(conn, case_id, "SYSTEM", "NOTIFICATION_SENT", {"channel": channel, "recipient": recipient})


def summary(conn, case_id: str) -> dict:
    c = get_case(conn, case_id)
    g = c["guardrails"] or {}
    rec = ((c["agents"].get("recommendation") or {}).get("output") or {})
    return {
        "case_id": c["case_id"], "alert_id": c["alert_id"], "model": c["model_label"], "orchestrator": c["orchestrator"],
        "status": c["status"], "route": c["route"], "route_label": ROUTE_LABEL.get(c["route"] or "", ""),
        "risk_score": c["risk_score"], "risk_band": c["risk_band"], "decision": c["decision"],
        "confidence": c["confidence"], "reason": g.get("reason"), "flags": g.get("flags", []),
        "interventions": g.get("interventions", []), "rationale": rec.get("rationale"),
        "recommended_actions": rec.get("recommended_actions", []), "qa_sampled": bool(c["qa_sampled"]),
        "total_latency_ms": c["total_latency_ms"], "error": c["error"],
    }


# ------------------------------------------------------------------------------------- full run
def _run_step(case_id: str, agent: str) -> dict:
    with db.session() as conn:
        return step_agent(conn, case_id, agent)


def investigate(alert_id: str, model_label: str, orchestrator: str = "python") -> dict:
    """In-process run of the same steps n8n performs. Each step commits, so the UI can follow progress."""
    t0 = time.perf_counter()
    with db.session() as conn:
        case_id = start_case(conn, alert_id, model_label, orchestrator)
    with db.session() as conn:
        ev = step_evidence(conn, case_id)
    if ev["ok"]:
        # Agents 1 and 2 are independent: run them side by side, then Risk & Policy, then Recommendation.
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = list(pool.map(lambda a: _run_step(case_id, a), ["transaction_analysis", "customer_behaviour"]))
        if all(r["ok"] for r in first):
            for agent in ("risk_policy", "recommendation"):
                if not _run_step(case_id, agent)["ok"]:
                    break
    with db.session() as conn:
        step_route(conn, case_id)
        out = apply_route(conn, case_id)
    out["wall_ms"] = int((time.perf_counter() - t0) * 1000)
    return out


# ------------------------------------------------------------------------------------- HITL
def human_decision(conn, case_id: str, decision: str, user: str, role: str, reason: str) -> dict:
    case = get_case(conn, case_id)
    decision = decision.upper()
    role = role.upper()
    if role not in guardrails.ROLES or role == "AUDITOR":
        raise CaseError(f"Role {role} is not permitted to decide cases", 403)
    if case["status"] == "DECIDED":
        raise CaseError("Case already decided", 409)
    route = case["route"] or "X"
    allowed = guardrails.allowed_decisions(role, route)
    if not allowed:
        raise CaseError(f"{guardrails.ROLES[role]} cannot decide a route-{route} case; requires Senior Investigator", 403)
    if decision not in allowed:
        raise CaseError(f"Decision {decision} not permitted for {role}", 403)
    is_override = bool(case["decision"]) and decision != case["decision"]
    if (is_override or route == "X") and len((reason or "").strip()) < 15:
        raise CaseError("A written reason (min 15 characters) is required to override the AI recommendation "
                        "or to decide an exception case", 422)
    if not user or not user.strip():
        raise CaseError("Investigator ID is required", 422)

    actions = case["actions"] or []
    if role == "ANALYST" and decision == "ESCALATE":
        _notify(conn, case_id, "L2_QUEUE", "Senior Fraud Investigator on duty", f"L1 referred case {case_id} to L2: {reason}")
        _save(conn, case_id, status="ESCALATED", route="E", human_reason=reason, decided_by=None)
        audit.log(conn, case_id, user, "REFERRED_TO_L2", {"reason": reason}, role=role)
        return {**summary(conn, case_id), "message": "Referred to L2 - blocking actions require a Senior Investigator."}

    permitted, withheld = guardrails.actions_for(decision, role)
    for a in permitted:
        actions.append({"action": a, "label": guardrails.ACTIONS[a]["label"], "by": user, "role": role,
                        "at": _now(), "status": "EXECUTED (simulated)"})
    str_draft = None
    if "draft_str_for_compliance" in permitted:
        rec = ((case["agents"].get("recommendation") or {}).get("output") or {})
        str_draft = (f"STR DRAFT (for compliance review, not filed)\nCase: {case_id}\nAlert: {case['alert_id']}\n"
                     f"Typology: {((case['agents'].get('risk_policy') or {}).get('output') or {}).get('fraud_typology')}\n"
                     f"Grounds for suspicion: {rec.get('rationale')}\nInvestigator: {user} ({role})\nReason: {reason}")
    status = "DECIDED"
    _save(conn, case_id, status=status, human_decision=decision, human_reason=reason, decided_by=user,
          decided_role=role, decided_at=_now(), actions=actions)
    final_alert_status = {"PROCEED": "CLOSED_GENUINE", "VERIFY": "CUSTOMER_VERIFICATION", "HOLD": "ON_HOLD",
                          "ESCALATE": "CONFIRMED_SUSPICIOUS"}[decision]
    conn.execute("UPDATE alerts SET status=? WHERE alert_id=?", (final_alert_status, case["alert_id"]))
    audit.log(conn, case_id, user, "HUMAN_DECISION",
              {"decision": decision, "ai_decision": case["decision"], "override": is_override, "reason": reason,
               "actions_executed": permitted, "actions_withheld": withheld}, role=role)
    return {**summary(conn, case_id), "human_decision": decision, "override": is_override,
            "actions_executed": permitted, "actions_withheld": withheld, "str_draft": str_draft}
