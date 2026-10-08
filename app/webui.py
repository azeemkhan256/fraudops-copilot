"""Light web app: one HTML page served by the API itself (no Streamlit process) plus a few helper endpoints.

Open http://127.0.0.1:8000 . Investigations run in a background thread; the page polls progress, so each
agent shows up the moment it finishes.
"""
from __future__ import annotations

import csv
import json
import threading
import time
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from . import config, db, orchestrator
from .schemas import AGENT_ORDER

ROOT = config.ROOT
PAGE = ROOT / "ui" / "web" / "index.html"
router = APIRouter()
JOBS: dict[str, dict] = {}


class RunRequest(BaseModel):
    alert_id: str
    model: str
    orchestrator: str = "python"


class DecideRequest(BaseModel):
    case_id: str
    decision: str
    investigator: str
    role: str
    reason: str = ""
    orchestrator: str = "python"


class Settings(BaseModel):
    cache: bool


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def page():
    return FileResponse(PAGE)


def n8n_running() -> bool:
    try:
        return httpx.get(config.N8N_WEBHOOK_URL.split("/webhook")[0] + "/healthz", timeout=1.5).status_code == 200
    except httpx.HTTPError:
        return False


@router.get("/ui/status")
def status():
    return {"n8n": n8n_running(), "cache": config.LLM_CACHE_ENABLED, "default_model": config.DEFAULT_MODEL,
            "benchmark": (ROOT / "benchmark" / "results" / "summary.json").exists()}


@router.post("/ui/settings")
def settings(body: Settings):
    config.LLM_CACHE_ENABLED = body.cache
    return {"cache": config.LLM_CACHE_ENABLED}


def _run(job_id: str, req: RunRequest) -> None:
    job = JOBS[job_id]
    try:
        if req.orchestrator == "n8n":
            r = httpx.post(config.N8N_WEBHOOK_URL, timeout=1800,
                           json={"alert_id": req.alert_id, "model": req.model, "api_base": config.N8N_API_BASE})
            data = r.json()
            job["result"] = data[0] if isinstance(data, list) and data else data
        else:
            job["result"] = orchestrator.investigate(req.alert_id, req.model, "python")
    except Exception as e:  # noqa: BLE001 - surface any failure to the page
        job["result"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    job["done"] = True
    job["finished"] = time.time()


@router.post("/ui/investigate")
def investigate(req: RunRequest):
    config.get_model(req.model)
    if req.orchestrator == "n8n" and not n8n_running():
        raise HTTPException(409, "n8n is not running - choose Orchestrator: Fast · built-in engine, or start with launcher option 9")
    for old in [k for k, j in JOBS.items() if j["done"] and time.time() - j["finished"] > 3600]:
        JOBS.pop(old, None)  # keep memory flat during long demo sessions
    job_id = uuid.uuid4().hex[:10]
    JOBS[job_id] = {"alert_id": req.alert_id, "started": time.time(), "done": False, "result": None}
    threading.Thread(target=_run, args=(job_id, req), daemon=True).start()
    return {"job_id": job_id}


@router.get("/ui/jobs/{job_id}")
def job(job_id: str):
    j = JOBS.get(job_id)
    if not j:
        raise HTTPException(404, "unknown job")
    out = {"done": j["done"], "result": j["result"], "elapsed_s": round((j.get("finished") or time.time()) - j["started"], 1)}
    # live progress: the newest case for this alert created after the job started
    started = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(j["started"] - 2))
    with db.session() as conn:
        row = db.row(conn, "SELECT case_id FROM cases WHERE alert_id=? AND created_at>=? ORDER BY created_at DESC LIMIT 1",
                      (j["alert_id"], started))
        if row:
            c = orchestrator.get_case(conn, row["case_id"])
            ev = c.get("evidence") or {}
            out["progress"] = {
                "case_id": c["case_id"], "status": c["status"], "error": c["error"],
                "evidence": {"facts": len(ev.get("facts", [])), "rule_score": ev.get("rule_score"),
                             "injection": ev.get("injection_detected")} if ev else None,
                "agents": {a: {"ok": r.get("ok"), "latency_ms": r.get("latency_ms"), "cached": r.get("cached", False),
                               "error": r.get("error")} for a, r in (c.get("agents") or {}).items() if r},
                "route": c["route"],
            }
    return out


@router.post("/ui/decide")
def decide(req: DecideRequest):
    payload = req.model_dump(exclude={"orchestrator"})
    if req.orchestrator == "n8n" and n8n_running():
        r = httpx.post(config.N8N_DECISION_WEBHOOK_URL, json={**payload, "api_base": config.N8N_API_BASE}, timeout=60)
        data = r.json()
        return data[0] if isinstance(data, list) and data else data
    with db.session() as conn:
        try:
            return {"ok": True, **orchestrator.human_decision(conn, req.case_id, req.decision, req.investigator,
                                                              req.role.upper(), req.reason)}
        except orchestrator.CaseError as e:
            return {"ok": False, "error": str(e), "status_code": e.status_code}


@router.get("/ui/benchmark")
def benchmark():
    res = ROOT / "benchmark" / "results"
    if not (res / "summary.json").exists():
        return {"available": False}
    out = {"available": True, "summary": json.loads((res / "summary.json").read_text(encoding="utf-8"))}
    if (res / "per_case.csv").exists():
        with (res / "per_case.csv").open(encoding="utf-8") as fh:
            out["per_case"] = list(csv.DictReader(fh))
    charts = res / "charts"
    out["charts"] = sorted(p.name for p in charts.glob("*.png")) if charts.exists() else []
    return out


@router.get("/ui/file/{kind}/{name}", include_in_schema=False)
def file(kind: str, name: str):
    base = {"diagram": ROOT / "docs" / "diagrams", "chart": ROOT / "benchmark" / "results" / "charts"}.get(kind)
    path = (base / name) if base else None
    if not path or path.suffix != ".png" or path.parent != base or not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


@router.get("/ui/diagrams")
def diagrams():
    d = ROOT / "docs" / "diagrams"
    return sorted(p.name for p in d.glob("*.png")) if d.exists() else []


@router.get("/ui/agents")
def agents():
    return AGENT_ORDER
