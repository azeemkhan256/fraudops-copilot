"""FastAPI service: bank tools, agent endpoints, routing policy, case management, HITL and audit.

n8n orchestrates the investigation by calling these endpoints in sequence; the web app (app/webui.py +
ui/web/index.html, served at /) uses them for queues, decisions and the audit trail. Run:  uvicorn app.api:app --port 8000
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import audit, config, db, guardrails, llm, orchestrator, seed_data, webui
from .orchestrator import CaseError
from .schemas import AGENT_ORDER, AGENT_TITLES

@asynccontextmanager
async def lifespan(_: FastAPI):
    if not config.DB_PATH.exists():
        seed_data.reset_database()
    yield


app = FastAPI(title="Governed Agentic AI - Fraud Investigation API", version="1.0.0", lifespan=lifespan,
              description="Tools, agents, guardrails and case management for the fraud-investigation PoC.")


app.include_router(webui.router)


@app.exception_handler(CaseError)
async def _case_error(_: Request, exc: CaseError):
    return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": str(exc)})


class StartCase(BaseModel):
    alert_id: str
    model: str = config.DEFAULT_MODEL
    orchestrator: str = "n8n"


class CaseRef(BaseModel):
    case_id: str


class Decision(BaseModel):
    case_id: str
    decision: str
    investigator: str = Field(min_length=1)
    role: str
    reason: str = ""


class Investigate(BaseModel):
    alert_id: str
    model: str = config.DEFAULT_MODEL


# ------------------------------------------------------------------------------ info
@app.get("/health")
def health():
    return {"ok": True, "db": str(config.DB_PATH), "thresholds": {
        "auto_close_max_risk": config.AUTO_CLOSE_MAX_RISK, "auto_close_min_confidence": config.AUTO_CLOSE_MIN_CONFIDENCE,
        "escalate_min_risk": config.ESCALATE_MIN_RISK, "qa_sample_rate": config.QA_SAMPLE_RATE}}


@app.get("/models")
def models():
    out = []
    for m in config.load_models():
        ready, note = llm.provider_ready(m)
        out.append({**m, "ready": ready, "note": note})
    return out


@app.get("/alerts")
def alerts():
    with db.session() as conn:
        rows = db.rows(conn, "SELECT a.*, c.segment, c.home_city FROM alerts a LEFT JOIN customers c "
                             "ON a.customer_id=c.customer_id ORDER BY a.alert_id")
        for r in rows:
            r["trigger_rules"] = db.loads(r["trigger_rules"], [])
            t = db.row(conn, "SELECT amount, channel, ts FROM transactions WHERE txn_id=?", (r["txn_id"],)) or {}
            r.update({"amount": t.get("amount"), "channel": t.get("channel")})
        return rows


@app.get("/rules")
def rules():
    with db.session() as conn:
        return db.rows(conn, "SELECT * FROM fraud_rules ORDER BY rule_id")


# ------------------------------------------------------------------------------ orchestration steps (called by n8n)
@app.post("/cases/start")
def cases_start(body: StartCase):
    with db.session() as conn:
        case_id = orchestrator.start_case(conn, body.alert_id, body.model, body.orchestrator)
    return {"ok": True, "case_id": case_id, "alert_id": body.alert_id, "model": body.model}


@app.post("/tools/evidence")
def tools_evidence(body: CaseRef):
    with db.session() as conn:
        return orchestrator.step_evidence(conn, body.case_id)


@app.post("/agents/{agent}")
def agents_run(agent: str, body: CaseRef):
    with db.session() as conn:
        return orchestrator.step_agent(conn, body.case_id, agent)


@app.post("/guardrails/route")
def guardrails_route(body: CaseRef):
    with db.session() as conn:
        return orchestrator.step_route(conn, body.case_id)


@app.post("/cases/{case_id}/apply/{route}")
def cases_apply(case_id: str, route: str):
    with db.session() as conn:
        return orchestrator.apply_route(conn, case_id, route)


# ------------------------------------------------------------------------------ Python fallback orchestrator
@app.post("/investigate")
def investigate(body: Investigate):
    return orchestrator.investigate(body.alert_id, body.model, "python")


# ------------------------------------------------------------------------------ case management & HITL
@app.get("/cases")
def cases(status: str | None = None, route: str | None = None):
    sql, params = "SELECT case_id FROM cases WHERE 1=1", []
    if status:
        sql += " AND status=?"
        params.append(status)
    if route:
        sql += " AND route=?"
        params.append(route)
    with db.session() as conn:
        ids = [r["case_id"] for r in db.rows(conn, sql + " ORDER BY created_at DESC", tuple(params))]
        return [orchestrator.summary(conn, i) for i in ids]


@app.get("/cases/{case_id}")
def case_detail(case_id: str):
    with db.session() as conn:
        c = orchestrator.get_case(conn, case_id)
        c["agent_titles"] = AGENT_TITLES
        c["agent_order"] = AGENT_ORDER
        c["audit"] = db.rows(conn, "SELECT seq, ts, actor, actor_role, action, details FROM audit_log WHERE case_id=? "
                                   "ORDER BY seq", (case_id,))
        return c


@app.post("/cases/decision")
def case_decision(body: Decision, x_role: str | None = Header(default=None)):
    role = (x_role or body.role).upper()
    with db.session() as conn:
        return {"ok": True, **orchestrator.human_decision(conn, body.case_id, body.decision, body.investigator, role,
                                                          body.reason)}


@app.post("/cases/sla/check")
def sla_check(minutes: int = 15):
    """Cases waiting for a human longer than the SLA are re-escalated to the fraud operations head."""
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
    with db.session() as conn:
        late = db.rows(conn, "SELECT case_id, status, updated_at FROM cases WHERE status IN ('ESCALATED','PENDING_REVIEW',"
                             "'EXCEPTION') AND updated_at < ?", (cutoff,))
        for c in late:
            already = db.row(conn, "SELECT 1 AS x FROM notifications WHERE case_id=? AND channel='SLA_BREACH'", (c["case_id"],))
            if not already:
                conn.execute("INSERT INTO notifications (ts, case_id, channel, recipient, message) VALUES (?,?,?,?,?)",
                             (audit.now(), c["case_id"], "SLA_BREACH", "Head of Fraud Operations",
                              f"{c['case_id']} ({c['status']}) not decided within {minutes} min"))
                audit.log(conn, c["case_id"], "n8n:sla_monitor", "SLA_BREACH_ESCALATED", {"minutes": minutes})
        return {"breaches": len(late), "cases": [c["case_id"] for c in late]}


@app.get("/roles")
def roles():
    return {"roles": guardrails.ROLES, "actions": guardrails.ACTIONS, "decision_actions": guardrails.DECISION_ACTIONS}


@app.get("/notifications")
def notifications():
    with db.session() as conn:
        return db.rows(conn, "SELECT * FROM notifications ORDER BY id DESC LIMIT 100")


@app.get("/audit")
def audit_log(limit: int = 300):
    with db.session() as conn:
        return db.rows(conn, "SELECT * FROM audit_log ORDER BY seq DESC LIMIT ?", (limit,))


@app.get("/audit/verify")
def audit_verify():
    with db.session() as conn:
        return audit.verify_chain(conn)


@app.post("/admin/reset")
def admin_reset(full: bool = False):
    if full:
        seed_data.reset_database()
    else:
        seed_data.reset_cases_only()
    return {"ok": True, "full": full}


@app.exception_handler(KeyError)
async def _key_error(_: Request, exc: KeyError):
    return JSONResponse(status_code=400, content={"ok": False, "error": str(exc).strip("'\"")})
