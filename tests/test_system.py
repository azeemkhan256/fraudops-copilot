"""Automated tests: pipeline routing, guardrails, HITL/RBAC, audit integrity, LLM-client robustness, API.

Run:  python -m pytest -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import audit, config, db, guardrails, llm, orchestrator, seed_data, tools  # noqa: E402
from app.agents import run_agent  # noqa: E402

GT = json.loads((ROOT / "benchmark" / "ground_truth.json").read_text())["cases"]


@pytest.fixture(scope="module", autouse=True)
def temp_db(tmp_path_factory):
    original, cache = config.DB_PATH, config.LLM_CACHE_ENABLED
    config.DB_PATH = tmp_path_factory.mktemp("db") / "test.db"
    config.LLM_CACHE_ENABLED = False
    seed_data.reset_database()
    yield
    config.DB_PATH, config.LLM_CACHE_ENABLED = original, cache


# ------------------------------------------------------------------ end-to-end routing (normal / ambiguous / high-risk)
@pytest.mark.parametrize("alert_id", sorted(GT))
def test_every_case_routes_acceptably_with_mock(alert_id):
    res = orchestrator.investigate(alert_id, "mock-heuristic")
    assert res["route"] in GT[alert_id]["acceptable_routes"], res


def test_data_exception_goes_to_exception_queue():
    res = orchestrator.investigate("ALT-2003", "mock-heuristic")
    assert res["route"] == "X" and res["status"] == "EXCEPTION"
    assert "unavailable" in (res["error"] or "")


# ------------------------------------------------------------------ data guardrails
def test_pii_redaction_and_no_pii_in_prompts():
    text = "Call +91 9876543210 or mail a.b@example.com, PAN ABCDE1234F, acct 123456789012"
    red = guardrails.redact_pii(text)
    assert "9876543210" not in red and "example.com" not in red and "ABCDE1234F" not in red and "123456789012" not in red
    with db.session() as conn:
        pack = tools.build_evidence_pack(conn, "ALT-2005")
    blob = json.dumps(tools.public_pack(pack))
    assert not guardrails.pii_leak_check(blob, pack["pii_values"]), "raw PII leaked into the evidence pack"


# ------------------------------------------------------------------ model guardrails
def test_prompt_injection_is_quarantined():
    with db.session() as conn:
        pack = tools.build_evidence_pack(conn, "ALT-3004")
    assert pack["injection_detected"]
    assert "ignore all previous instructions" not in pack["untrusted_remarks"].lower()
    assert "invoice 4471" in pack["untrusted_remarks"].lower()


def test_grounding_detects_unsupported_claims_and_bad_citations():
    with db.session() as conn:
        pack = tools.build_evidence_pack(conn, "ALT-1001")
    bad = guardrails.grounding_check("Customer suffered a SIM swap and the payee is on the watchlist", ["E2", "E99"], pack)
    assert set(bad["unsupported_claims"]) == {"sim_swap", "watchlist"} and bad["invalid_citations"] == ["E99"]
    ok = guardrails.grounding_check("No SIM swap and the payee is not on the watchlist; mule indicators: none", ["E2"], pack)
    assert ok["grounded"], ok


def _outputs(decision="PROCEED", llm_risk=10, conf=0.9, anomaly=10):
    return {"transaction_analysis": {"anomaly_score": anomaly}, "customer_behaviour": {"behaviour_risk": 0},
            "risk_policy": {"risk_score": llm_risk}, "recommendation": {"decision": decision, "confidence": conf}}


def _pack(rule_score=10, sev="LOW", injection=False):
    return {"rule_score": rule_score, "rule_hits": [{"severity": sev}], "injection_detected": injection}


def test_routing_policy():
    ok = {a: {"grounded": True} for a in _outputs()}
    assert guardrails.decide_route(_pack(), _outputs(), ok)["route"] == "A"
    assert guardrails.decide_route(_pack(), _outputs(conf=0.6), ok)["route"] == "H"            # low confidence
    assert guardrails.decide_route(_pack(injection=True), _outputs(), ok)["route"] == "H"       # injection flag
    r = guardrails.decide_route(_pack(rule_score=65, sev="HIGH"), _outputs(llm_risk=5), ok)     # model under-scores
    assert r["final_risk"] == 65 and r["route"] == "H" and "recommendation_inconsistent_with_risk" in r["flags"]
    assert guardrails.decide_route(_pack(rule_score=40, sev="CRITICAL"), _outputs("VERIFY", 40), ok)["route"] == "E"
    assert guardrails.decide_route(_pack(), _outputs("ESCALATE", 20), ok)["route"] == "E"
    bad = {**ok, "recommendation": {"grounded": False}}
    assert guardrails.decide_route(_pack(), _outputs(), bad)["route"] == "H"                    # hallucination flag
    failed = {**_outputs(), "risk_policy": None, "recommendation": None}
    assert guardrails.decide_route(_pack(), failed, ok)["route"] == "X"


# ------------------------------------------------------------------ action guardrails / HITL
def test_rbac_and_override_rules():
    res = orchestrator.investigate("ALT-3001", "mock-heuristic")
    cid = res["case_id"]
    with db.session() as conn:
        with pytest.raises(orchestrator.CaseError) as e:
            orchestrator.human_decision(conn, cid, "ESCALATE", "analyst.01", "ANALYST", "")
        assert e.value.status_code == 403
        with pytest.raises(orchestrator.CaseError):
            orchestrator.human_decision(conn, cid, "ESCALATE", "aud.01", "AUDITOR", "")
        with pytest.raises(orchestrator.CaseError) as e:
            orchestrator.human_decision(conn, cid, "PROCEED", "senior.01", "SENIOR", "ok")   # override, reason too short
        assert e.value.status_code == 422
        out = orchestrator.human_decision(conn, cid, "ESCALATE", "senior.01", "SENIOR", "Confirmed takeover")
        assert "block_card_or_channel" in out["actions_executed"] and out["str_draft"]


def test_analyst_escalation_is_referral_without_blocking():
    res = orchestrator.investigate("ALT-2001", "mock-heuristic")
    with db.session() as conn:
        out = orchestrator.human_decision(conn, res["case_id"], "ESCALATE", "analyst.01", "ANALYST",
                                          "Customer unreachable, referring to L2")
    assert out["status"] == "ESCALATED" and out["route"] == "E"


def test_audit_chain_detects_tampering():
    with db.session() as conn:
        assert audit.verify_chain(conn)["valid"]
        conn.execute("UPDATE audit_log SET details='tampered' WHERE seq=(SELECT MIN(seq) FROM audit_log)")
    with db.session() as conn:
        assert not audit.verify_chain(conn)["valid"]
        # restore a clean chain for later tests
        conn.execute("DELETE FROM audit_log")


# ------------------------------------------------------------------ LLM client robustness (fake OpenAI-compatible server)
class FakeServer:
    def __init__(self, replies):
        self.replies = list(replies)
        self.bodies = []

    def __call__(self, url, json=None, headers=None, timeout=None):  # mimics httpx.post
        self.bodies.append(json)
        status, content = self.replies.pop(0)
        if status != 200:
            return httpx.Response(status, text=content, headers={"retry-after": "0"} if status == 429 else {})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}], "usage": {"total_tokens": 42}})


def test_llm_client_handles_rate_limit_bad_params_think_tags_and_repair(monkeypatch):
    with db.session() as conn:
        pack = tools.build_evidence_pack(conn, "ALT-1002")
    good = json.dumps({"anomaly_score": 12, "anomalies": [], "mitigating_factors": [],
                       "summary": "Rent to a long-standing beneficiary; amount revised per new lease."})
    fake = FakeServer([(429, "rate limited, try again in 0s"), (400, "response_format not supported"),
                       (200, "<think>reasoning...</think>```json\n{\"anomaly_score\": \"high\"}\n```"),
                       (200, "Sure! Here it is: " + good)])
    monkeypatch.setattr(httpx, "post", fake)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    cfg = {"label": "fake", "provider": "groq", "model": "fake-model"}
    res = run_agent("transaction_analysis", cfg, tools.public_pack(pack) | {"signals": pack["signals"]}, {}, pack["pii_values"])
    assert res["ok"] and res["attempts"] == 2 and not res["valid_first_try"]
    assert "response_format" not in fake.bodies[-1]
    assert res["output"]["anomaly_score"] == 12


def test_llm_failure_routes_to_exception(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("provider down")
    monkeypatch.setattr(httpx, "post", boom)
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setenv("CEREBRAS_API_KEY", "test-key")
    res = orchestrator.investigate("ALT-1004", "gpt-oss-120b")
    assert res["route"] == "X"


# ------------------------------------------------------------------ API used by n8n
def test_api_step_sequence():
    from fastapi.testclient import TestClient
    from app.api import app
    c = TestClient(app)
    cid = c.post("/cases/start", json={"alert_id": "ALT-3002", "model": "mock-heuristic"}).json()["case_id"]
    assert c.post("/tools/evidence", json={"case_id": cid}).json()["ok"]
    for a in ("transaction_analysis", "customer_behaviour", "risk_policy", "recommendation"):
        assert c.post(f"/agents/{a}", json={"case_id": cid}).json()["ok"]
    route = c.post("/guardrails/route", json={"case_id": cid}).json()["route"]
    assert route == "E"
    assert c.post(f"/cases/{cid}/apply/A").status_code == 409          # cannot bypass the policy decision
    assert c.post(f"/cases/{cid}/apply/E").json()["status"] == "ESCALATED"
    r = c.post("/cases/decision", json={"case_id": cid, "decision": "ESCALATE", "investigator": "a1", "role": "ANALYST"})
    assert r.status_code == 403


# ------------------------------------------------------------------ Google AI Studio (Gemma) native client
def test_gemini_native_client_fallbacks(monkeypatch):
    from app import llm as L
    calls = []

    def fake(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "body": json, "headers": headers})
        n = len(calls)
        if n == 1:
            return httpx.Response(429, text='{"error":{"details":[{"retryDelay":"0s"}]}}')
        if n == 2:
            return httpx.Response(400, text='{"error":{"message":"Developer instruction is not enabled for models/gemma-3-27b-it"}}')
        if n == 3:
            return httpx.Response(400, text='{"error":{"message":"JSON mode is not enabled for models/gemma-3-27b-it"}}')
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [
            {"text": "thinking...", "thought": True}, {"text": '{"ok": true}'}]}}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5, "totalTokenCount": 15}})

    monkeypatch.setattr(httpx, "post", fake)
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    cfg = {"label": "gemma-test", "provider": "gemini", "model": "gemma-3-27b-it"}
    r = L.chat(cfg, [{"role": "system", "content": "SYS"}, {"role": "user", "content": "hi"}])
    assert L.parse_json(r["content"]) == {"ok": True} and r["usage"]["total_tokens"] == 15
    last = calls[-1]
    assert last["url"].endswith("/models/gemma-3-27b-it:generateContent") and last["headers"]["x-goog-api-key"] == "k"
    assert "systemInstruction" not in last["body"] and last["body"]["contents"][0]["parts"][0]["text"].startswith("SYS")
    assert "responseMimeType" not in last["body"]["generationConfig"]
    assert set(r["dropped_params"]) == {"system", "json"}



# ------------------------------------------------------------------ speed features
def test_parallel_agents_keep_every_output_and_a_valid_audit_chain():
    with db.session() as conn:
        conn.execute("DELETE FROM audit_log")
    for a in ("ALT-3001", "ALT-1002", "ALT-2002"):
        res = orchestrator.investigate(a, "mock-heuristic")
        with db.session() as conn:
            case = orchestrator.get_case(conn, res["case_id"])
        assert all((case["agents"].get(x) or {}).get("ok") for x in
                   ("transaction_analysis", "customer_behaviour", "risk_policy", "recommendation")), a
    with db.session() as conn:
        assert audit.verify_chain(conn)["valid"]


def test_answer_cache_reuses_identical_prompts(monkeypatch, tmp_path):
    calls = []

    def fake(url, json=None, headers=None, timeout=None):
        calls.append(1)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]})
    monkeypatch.setattr(httpx, "post", fake)
    monkeypatch.setattr(config, "LLM_CACHE_ENABLED", True)
    monkeypatch.setattr(config, "LLM_CACHE_DIR", tmp_path)
    monkeypatch.setenv("CEREBRAS_API_KEY", "k")
    cfg = {"label": "cache-test", "provider": "cerebras", "model": "m"}
    msgs = [{"role": "user", "content": "same prompt"}]
    a, b = llm.chat(cfg, msgs), llm.chat(cfg, msgs)
    assert len(calls) == 1 and not a["cached"] and b["cached"] and b["latency_ms"] == 0


def test_unsupported_thinking_setting_is_dropped_not_json_mode(monkeypatch):
    bodies = []

    def fake(url, json=None, headers=None, timeout=None):
        bodies.append(json)
        if len(bodies) == 1:
            return httpx.Response(400, text='{"error":{"message":"Invalid JSON payload received. Unknown name \\"thinking_config\\""}}')
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]})
    monkeypatch.setattr(httpx, "post", fake)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    cfg = {"label": "think-test", "provider": "gemini", "model": "gemma-4-26b-a4b-it",
           "extra": {"thinkingConfig": {"thinkingLevel": "minimal"}}}
    r = llm.chat(cfg, [{"role": "user", "content": "x"}])
    assert r["dropped_params"] == ["thinkingConfig"]
    assert "thinkingConfig" not in bodies[-1]["generationConfig"] and "responseMimeType" in bodies[-1]["generationConfig"]


# ------------------------------------------------------------------ light web app (served by the API itself)
def test_web_app_runs_a_case_live_and_enforces_hitl_rules():
    import time
    from fastapi.testclient import TestClient
    from app.api import app
    c = TestClient(app)
    page = c.get("/")
    assert page.status_code == 200 and "FraudOps Console" in page.text
    job = c.post("/ui/investigate", json={"alert_id": "ALT-2001", "model": "mock-heuristic"}).json()["job_id"]
    for _ in range(100):
        j = c.get(f"/ui/jobs/{job}").json()
        if j["done"]:
            break
        time.sleep(0.05)
    assert j["done"] and j["result"]["route"] == "H"
    assert set(j["progress"]["agents"]) == {"transaction_analysis", "customer_behaviour", "risk_policy", "recommendation"}
    cid = j["result"]["case_id"]
    base = {"case_id": cid, "investigator": "INV-9", "role": "ANALYST"}
    blocked = c.post("/ui/decide", json={**base, "decision": "ESCALATE"}).json()       # override without a reason
    assert blocked["ok"] is False and "reason" in blocked["error"]
    assert c.post("/ui/decide", json={**base, "decision": "VERIFY", "role": "AUDITOR"}).json()["ok"] is False
    ok = c.post("/ui/decide", json={**base, "decision": "VERIFY"}).json()
    assert ok["ok"] and ok["actions_executed"] == ["send_verification_request"]
    assert c.get("/ui/file/diagram/..%2F..%2F.env").status_code == 404                # no path traversal


def test_retired_model_gives_a_clear_error_and_routes_to_exception(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(
        404, text='{"error":{"code":404,"message":"models/gemma-3-27b-it is not found for API version v1beta"}}'))
    cfg = {"label": "retired-test", "provider": "gemini", "model": "gemma-3-27b-it"}
    with pytest.raises(llm.LLMError, match="not available from gemini"):
        llm._chat(cfg, [{"role": "user", "content": "hi"}])
