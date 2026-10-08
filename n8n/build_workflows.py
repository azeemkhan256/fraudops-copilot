"""Generates the n8n workflow JSON files (import them in n8n: Workflows -> Import from File).

python n8n/build_workflows.py
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

OUT = Path(__file__).resolve().parent
API = "$('Config').first().json.api_base"
CASE = "$('Orchestrator: Open Case').first().json.case_id"


def _id() -> str:
    return str(uuid.uuid4())


def webhook(name, path, pos):
    return {"parameters": {"httpMethod": "POST", "path": path, "responseMode": "responseNode", "options": {}},
            "id": _id(), "name": name, "type": "n8n-nodes-base.webhook", "typeVersion": 2, "position": pos,
            "webhookId": _id()}


def setnode(name, fields, pos, include_other=False):
    return {"parameters": {"mode": "manual", "assignments": {"assignments": [
        {"id": _id(), "name": k, "value": v, "type": t} for k, v, t in fields]},
        "includeOtherFields": include_other, "options": {}},
        "id": _id(), "name": name, "type": "n8n-nodes-base.set", "typeVersion": 3.4, "position": pos}


def http(name, url_expr, body_expr, pos, method="POST", on_error=True, never_error=False):
    params = {"method": method, "url": "={{ " + API + " }}" + url_expr, "options": {"timeout": 900000}}
    if body_expr is not None:
        params.update({"sendBody": True, "specifyBody": "json", "jsonBody": "={{ JSON.stringify(" + body_expr + ") }}"})
    if never_error:
        params["options"]["response"] = {"response": {"neverError": True}}
    node = {"parameters": params, "id": _id(), "name": name, "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.2, "position": pos}
    if on_error:
        node["onError"] = "continueErrorOutput"
    return node


def ifnode(name, left_expr, pos):
    return {"parameters": {"conditions": {
        "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2},
        "conditions": [{"id": _id(), "leftValue": "={{ " + left_expr + " }}", "rightValue": "",
                        "operator": {"type": "boolean", "operation": "true", "singleValue": True}}],
        "combinator": "and"}, "looseTypeValidation": True, "options": {}},
        "id": _id(), "name": name, "type": "n8n-nodes-base.if", "typeVersion": 2.2, "position": pos}


def switch(name, left_expr, keys, pos):
    rules = [{"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
                             "conditions": [{"id": _id(), "leftValue": "={{ " + left_expr + " }}", "rightValue": key,
                                             "operator": {"type": "string", "operation": "equals"}}],
                             "combinator": "and"},
              "renameOutput": True, "outputKey": label} for key, label in keys]
    return {"parameters": {"rules": {"values": rules}, "options": {}}, "id": _id(), "name": name,
            "type": "n8n-nodes-base.switch", "typeVersion": 3.2, "position": pos}


def respond(name, pos, code=None):
    opts = {"responseCode": code} if code else {}
    return {"parameters": {"respondWith": "firstIncomingItem", "options": opts}, "id": _id(), "name": name,
            "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.1, "position": pos}


def sticky(text, pos, w=360, h=180, color=7):
    return {"parameters": {"content": text, "height": h, "width": w, "color": color}, "id": _id(),
            "name": "Note " + _id()[:6], "type": "n8n-nodes-base.stickyNote", "typeVersion": 1, "position": pos}


def link(conns, src, dst, out=0):
    conns.setdefault(src, {"main": []})
    while len(conns[src]["main"]) <= out:
        conns[src]["main"].append([])
    conns[src]["main"][out].append({"node": dst, "type": "main", "index": 0})


def investigation_workflow() -> dict:
    case_body = "{ case_id: " + CASE + " }"
    n = [
        webhook("Fraud Alert Webhook", "fraud-investigate", [0, 300]),
        setnode("Config", [
            ("api_base", "={{ $json.body.api_base || 'http://127.0.0.1:8000' }}", "string"),
            ("alert_id", "={{ $json.body.alert_id }}", "string"),
            ("model", "={{ $json.body.model || 'mock-heuristic' }}", "string")], [220, 300]),
        http("Orchestrator: Open Case", "/cases/start",
             "{ alert_id: $json.alert_id, model: $json.model, orchestrator: 'n8n' }", [440, 300]),
        http("Tool: Gather Evidence & Run Rules", "/tools/evidence", case_body, [660, 300]),
        ifnode("Evidence complete?", "$json.ok", [880, 300]),
        http("Agent 1: Transaction Analysis", "/agents/transaction_analysis", case_body, [1100, 300]),
        ifnode("Agent 1 valid?", "$json.ok", [1320, 300]),
        http("Agent 2: Customer Behaviour", "/agents/customer_behaviour", case_body, [1540, 300]),
        ifnode("Agent 2 valid?", "$json.ok", [1760, 300]),
        http("Agent 3: Risk & Policy", "/agents/risk_policy", case_body, [1980, 300]),
        ifnode("Agent 3 valid?", "$json.ok", [2200, 300]),
        http("Agent 4: Recommendation", "/agents/recommendation", case_body, [2420, 300]),
        http("Guardrails: Routing Policy", "/guardrails/route", case_body, [2640, 300]),
        switch("Route Decision (A/H/E/X)", "$json.route",
               [("A", "A - Autonomous close"), ("H", "H - Human review"), ("E", "E - Escalate"), ("X", "X - Exception")],
               [2860, 300]),
        http("[A] Auto-close False Positive (+QA sample)", "/cases/{{ " + CASE + " }}/apply/A", None, [3100, 60]),
        http("[H] Queue for Fraud Investigator", "/cases/{{ " + CASE + " }}/apply/H", None, [3100, 220]),
        http("[E] Escalate to L2 + Notify", "/cases/{{ " + CASE + " }}/apply/E", None, [3100, 380]),
        http("[X] Exception Queue + Notify Ops", "/cases/{{ " + CASE + " }}/apply/X", None, [3100, 540]),
        respond("Return Case to UI", [3360, 300]),
        setnode("Technical Exception", [
            ("ok", "={{ false }}", "boolean"),
            ("route", "X", "string"),
            ("error", "={{ $json.error ? ($json.error.message || JSON.stringify($json.error)) : 'Service call failed' }}", "string"),
            ("case_id", "={{ $('Orchestrator: Open Case').isExecuted ? " + CASE + " : null }}", "string")],
            [1540, 820]),
        respond("Return Exception to UI", [1780, 820]),
        sticky("## Governed Agentic AI - Fraud Investigation\n**Fraud Alert -> Orchestrator -> Transaction Analysis Agent -> "
               "Customer Behaviour Agent -> Risk/Policy Agent -> Recommendation Agent -> Human Fraud Investigator -> Outcome**\n\n"
               "Marks: **[A]** autonomous, **[H]** human-in-the-loop, **[E]** escalation, **[X]** exception.\n"
               "Every HTTP call goes to the FastAPI service (tools, agents, guardrails, audit).",
               [-20, -120], 820, 230, 6),
        sticky("### [A] Autonomous steps\nEvidence gathering, rules engine, 4 LLM agents, schema + grounding checks, "
               "routing policy. Agents only *recommend*; they cannot act.", [1060, 120], 1500, 130, 4),
        sticky("### Routing policy (deterministic)\nE if ESCALATE / risk>=70 / critical rule\nA only if PROCEED, risk<30, "
               "confidence>=0.8, no flags\nX on missing data or agent failure\nH otherwise", [2600, 480], 380, 220, 3),
        sticky("### Exception handling\nAny failed service call leaves via the red error output and is returned "
               "as a technical exception (route X).", [1480, 960], 420, 130, 2),
    ]
    by = {x["name"]: x for x in n}
    c: dict = {}
    link(c, "Fraud Alert Webhook", "Config")
    link(c, "Config", "Orchestrator: Open Case")
    link(c, "Orchestrator: Open Case", "Tool: Gather Evidence & Run Rules")
    link(c, "Orchestrator: Open Case", "Technical Exception", 1)
    link(c, "Tool: Gather Evidence & Run Rules", "Evidence complete?")
    link(c, "Tool: Gather Evidence & Run Rules", "Technical Exception", 1)
    link(c, "Evidence complete?", "Agent 1: Transaction Analysis", 0)
    link(c, "Evidence complete?", "Guardrails: Routing Policy", 1)
    link(c, "Agent 1: Transaction Analysis", "Agent 1 valid?")
    link(c, "Agent 1: Transaction Analysis", "Technical Exception", 1)
    link(c, "Agent 1 valid?", "Agent 2: Customer Behaviour", 0)
    link(c, "Agent 1 valid?", "Guardrails: Routing Policy", 1)
    link(c, "Agent 2: Customer Behaviour", "Agent 2 valid?")
    link(c, "Agent 2: Customer Behaviour", "Technical Exception", 1)
    link(c, "Agent 2 valid?", "Agent 3: Risk & Policy", 0)
    link(c, "Agent 2 valid?", "Guardrails: Routing Policy", 1)
    link(c, "Agent 3: Risk & Policy", "Agent 3 valid?")
    link(c, "Agent 3: Risk & Policy", "Technical Exception", 1)
    link(c, "Agent 3 valid?", "Agent 4: Recommendation", 0)
    link(c, "Agent 3 valid?", "Guardrails: Routing Policy", 1)
    link(c, "Agent 4: Recommendation", "Guardrails: Routing Policy")
    link(c, "Agent 4: Recommendation", "Technical Exception", 1)
    link(c, "Guardrails: Routing Policy", "Route Decision (A/H/E/X)")
    link(c, "Guardrails: Routing Policy", "Technical Exception", 1)
    for i, name in enumerate(["[A] Auto-close False Positive (+QA sample)", "[H] Queue for Fraud Investigator",
                              "[E] Escalate to L2 + Notify", "[X] Exception Queue + Notify Ops"]):
        link(c, "Route Decision (A/H/E/X)", name, i)
        link(c, name, "Return Case to UI")
        link(c, name, "Technical Exception", 1)
    link(c, "Technical Exception", "Return Exception to UI")
    assert all(k in by for k in c)
    return {"id": "FraudInvOrch0001", "name": "Fraud Investigation Orchestrator (Agentic AI)", "nodes": n, "connections": c,
            "settings": {"executionOrder": "v1"}, "pinData": {}, "tags": []}


def decision_workflow() -> dict:
    body = ("{ case_id: $('Decision Webhook').first().json.body.case_id, decision: $('Decision Webhook').first().json.body.decision, "
            "investigator: $('Decision Webhook').first().json.body.investigator, role: $('Decision Webhook').first().json.body.role, "
            "reason: $('Decision Webhook').first().json.body.reason || '' }")
    n = [
        webhook("Decision Webhook", "fraud-decision", [0, 300]),
        setnode("Config", [("api_base", "={{ $json.body.api_base || 'http://127.0.0.1:8000' }}", "string")], [220, 300]),
        http("Validate Role & Record Decision", "/cases/decision", body, [440, 300], on_error=False, never_error=True),
        ifnode("Decision accepted?", "$json.ok === true", [660, 300]),
        switch("Outcome", "$json.human_decision",
               [("PROCEED", "PROCEED"), ("VERIFY", "VERIFY"), ("HOLD", "HOLD"), ("ESCALATE", "ESCALATE")], [880, 200]),
        setnode("Release Transaction & Close Alert", [("outcome", "Transaction released; alert closed as genuine.", "string")],
                [1120, 0], include_other=True),
        setnode("Send Step-up Verification", [("outcome", "Verification request sent on registered channel (simulated).", "string")],
                [1120, 160], include_other=True),
        setnode("Hold Payment + Verification Call", [("outcome", "Payment held (max 24h); verification call scheduled (simulated).", "string")],
                [1120, 320], include_other=True),
        setnode("Protective Actions + STR Draft", [("outcome", "L2 actions executed (simulated); STR draft sent to Compliance; customer advised on 1930.", "string")],
                [1120, 480], include_other=True),
        respond("Return Outcome", [1360, 240]),
        respond("Return Rejection", [880, 560], code=403),
        sticky("## [H] Human-in-the-Loop decision handler\nThe investigator's decision is validated (role-based access, "
               "mandatory reason for overrides, L2-only blocking actions) and written to the hash-chained audit log "
               "before any outcome action runs.", [-20, -40], 620, 180, 6),
    ]
    c: dict = {}
    link(c, "Decision Webhook", "Config")
    link(c, "Config", "Validate Role & Record Decision")
    link(c, "Validate Role & Record Decision", "Decision accepted?")
    link(c, "Decision accepted?", "Outcome", 0)
    link(c, "Decision accepted?", "Return Rejection", 1)
    for i, name in enumerate(["Release Transaction & Close Alert", "Send Step-up Verification",
                              "Hold Payment + Verification Call", "Protective Actions + STR Draft"]):
        link(c, "Outcome", name, i)
        link(c, name, "Return Outcome")
    return {"id": "HitlDecision0002", "name": "HITL Decision Handler", "nodes": n, "connections": c, "settings": {"executionOrder": "v1"},
            "pinData": {}, "tags": []}


def sla_workflow() -> dict:
    n = [
        {"parameters": {"rule": {"interval": [{"field": "minutes", "minutesInterval": 5}]}}, "id": _id(),
         "name": "Every 5 minutes", "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.2, "position": [0, 300]},
        {"parameters": {}, "id": _id(), "name": "Run now (manual test)", "type": "n8n-nodes-base.manualTrigger",
         "typeVersion": 1, "position": [0, 480]},
        setnode("Config", [("api_base", "http://127.0.0.1:8000", "string")], [220, 300]),
        http("Check SLA Breaches", "/cases/sla/check?minutes=15", "{}", [440, 300], on_error=False),
        ifnode("Any breaches?", "$json.breaches > 0", [660, 300]),
        setnode("Breaches Escalated", [("summary", "={{ $json.breaches + ' case(s) escalated to fraud operations head' }}", "string")],
                [880, 220]),
        sticky("## [E] SLA escalation monitor\nEscalated or pending cases not decided within 15 minutes are re-escalated "
               "to the fraud operations head (time-critical: funds can leave the bank within minutes).",
               [-20, 40], 560, 160, 3),
    ]
    c: dict = {}
    link(c, "Every 5 minutes", "Config")
    link(c, "Run now (manual test)", "Config")
    link(c, "Config", "Check SLA Breaches")
    link(c, "Check SLA Breaches", "Any breaches?")
    link(c, "Any breaches?", "Breaches Escalated", 0)
    return {"id": "SlaMonitor000003", "name": "SLA Escalation Monitor", "nodes": n, "connections": c, "settings": {"executionOrder": "v1"},
            "pinData": {}, "tags": []}


if __name__ == "__main__":
    for fname, wf in (("01_fraud_investigation_orchestrator.json", investigation_workflow()),
                      ("02_hitl_decision_handler.json", decision_workflow()),
                      ("03_sla_escalation_monitor.json", sla_workflow())):
        (OUT / fname).write_text(json.dumps(wf, indent=2), encoding="utf-8")
        print("wrote", fname, len(wf["nodes"]), "nodes")
