"""FraudOps Copilot - Streamlit UI for the governed agentic fraud-investigation PoC.

Run:  streamlit run ui/streamlit_app.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import config  # noqa: E402

st.set_page_config(page_title="FraudOps Copilot", page_icon="🛡️", layout="wide")

ROUTE_STYLE = {
    "A": ("Autonomous close", "#1f7a4d", "#e3f4ea"),
    "H": ("Human review", "#8a5a00", "#fdf1d8"),
    "E": ("Escalated to L2", "#a3242b", "#fbe3e4"),
    "X": ("Exception", "#4a4f8c", "#e7e8f7"),
}
BAND_COLOR = {"LOW": "#1f7a4d", "MEDIUM": "#b07400", "HIGH": "#c2410c", "CRITICAL": "#a3242b"}
DECISION_HELP = {
    "PROCEED": "Genuine - release / close as false positive",
    "VERIFY": "Step-up verification with the customer",
    "HOLD": "Hold the payment pending verification",
    "ESCALATE": "Refer to L2 / protective actions",
}

st.markdown("""
<style>
.block-container {padding-top: 1.6rem;}
.badge {display:inline-block;padding:2px 10px;border-radius:999px;font-size:0.8rem;font-weight:600;margin-right:6px}
.card {border:1px solid rgba(128,128,128,.25);border-radius:10px;padding:14px 16px;margin-bottom:10px}
.muted {opacity:.7;font-size:.85rem}
.kpi {font-size:1.6rem;font-weight:700;line-height:1.1}
.fact {font-family: ui-monospace, monospace; font-size: .82rem; padding:2px 0}
</style>""", unsafe_allow_html=True)


# ----------------------------------------------------------------------------- helpers
def api(method: str, path: str, **kw):
    url = st.session_state.api_url.rstrip("/") + path
    try:
        r = requests.request(method, url, timeout=kw.pop("timeout", 30), **kw)
    except requests.RequestException as e:
        st.error(f"Cannot reach the API at {url}. Start it with `uvicorn app.api:app --port 8000`. ({e})")
        st.stop()
    try:
        data = r.json()
    except ValueError:
        data = {"ok": False, "error": r.text[:300]}
    if r.status_code >= 400 and isinstance(data, dict):
        data.setdefault("ok", False)
        data.setdefault("error", data.get("detail", f"HTTP {r.status_code}"))
    return data


def badge(text: str, fg: str, bg: str) -> str:
    return f'<span class="badge" style="color:{fg};background:{bg};border:1px solid {fg}33">{text}</span>'


def route_badge(route: str | None) -> str:
    if not route:
        return badge("In progress", "#555", "#eee")
    label, fg, bg = ROUTE_STYLE[route]
    return badge(f"[{route}] {label}", fg, bg)


def band_badge(band: str | None, score) -> str:
    if not band:
        return ""
    c = BAND_COLOR.get(band, "#555")
    return badge(f"Risk {score} · {band}", c, c + "18")


def run_investigation(alert_id: str, model: str) -> dict:
    if st.session_state.orchestrator == "n8n":
        try:
            r = requests.post(st.session_state.n8n_url, json={"alert_id": alert_id, "model": model,
                                                               "api_base": st.session_state.api_url_for_n8n}, timeout=1800)
            data = r.json()
            if isinstance(data, list):
                data = data[0] if data else {}
            return data
        except (requests.RequestException, ValueError) as e:
            return {"ok": False, "error": f"n8n webhook call failed ({e}). Is the workflow active? "
                                          f"Switch to 'Python engine' in the sidebar to run without n8n."}
    return api("POST", "/investigate", json={"alert_id": alert_id, "model": model}, timeout=1800)


def submit_decision(case_id: str, decision: str, reason: str) -> dict:
    payload = {"case_id": case_id, "decision": decision, "investigator": st.session_state.user,
               "role": st.session_state.role, "reason": reason}
    if st.session_state.orchestrator == "n8n":
        try:
            r = requests.post(st.session_state.n8n_decision_url, json={**payload, "api_base": st.session_state.api_url_for_n8n},
                              timeout=60)
            data = r.json()
            return data[0] if isinstance(data, list) and data else data
        except (requests.RequestException, ValueError) as e:
            return {"ok": False, "error": f"n8n decision webhook failed: {e}"}
    return api("POST", "/cases/decision", json=payload)


# ----------------------------------------------------------------------------- sidebar
def _n8n_running() -> bool:
    try:
        return requests.get(config.N8N_WEBHOOK_URL.split("/webhook")[0] + "/healthz", timeout=1).ok
    except requests.RequestException:
        return False


def sidebar() -> None:
    ss = st.session_state
    ss.setdefault("api_url", config.API_URL)
    ss.setdefault("api_url_for_n8n", config.N8N_API_BASE)
    ss.setdefault("n8n_url", config.N8N_WEBHOOK_URL)
    ss.setdefault("n8n_decision_url", config.N8N_DECISION_WEBHOOK_URL)
    if "orchestrator" not in ss:
        ss.orchestrator = os.getenv("FRAUDOPS_ORCHESTRATOR") or ("n8n" if _n8n_running() else "python")
    ss.setdefault("user", "analyst.01")
    ss.setdefault("role", "ANALYST")
    with st.sidebar:
        st.markdown("### 🛡️ FraudOps Copilot")
        st.caption("Governed Agentic AI for fraud investigation · synthetic data only")
        models = api("GET", "/models")
        labels = [m["label"] for m in models]
        ready = {m["label"]: m for m in models}
        default = labels.index(config.DEFAULT_MODEL) if config.DEFAULT_MODEL in labels else 0
        ss.setdefault("model", labels[default])
        ss.model = st.selectbox("Model for the agents", labels, index=labels.index(ss.model) if ss.model in labels else 0,
                                format_func=lambda l: f"{l}  {'✅' if ready[l]['ready'] else '⚠️'}")
        m = ready[ss.model]
        st.caption(f"{m['family']} · provider: {m['provider']}" + ("" if m["ready"] else f" · {m['note']}"))
        ss.orchestrator = st.radio("Orchestrator", ["n8n", "python"], index=0 if ss.orchestrator == "n8n" else 1,
                                   format_func=lambda o: "n8n workflow (webhook)" if o == "n8n" else "Python engine (fallback)",
                                   horizontal=False)
        st.divider()
        st.markdown("**Signed-in investigator**")
        ss.user = st.text_input("Investigator ID", ss.user)
        ss.role = st.selectbox("Role", ["ANALYST", "SENIOR", "AUDITOR"], index=["ANALYST", "SENIOR", "AUDITOR"].index(ss.role),
                               format_func=lambda r: {"ANALYST": "Fraud Analyst (L1)", "SENIOR": "Senior Investigator (L2)",
                                                      "AUDITOR": "Auditor (read-only)"}[r])
        with st.expander("Connection settings"):
            ss.api_url = st.text_input("API URL (UI → API)", ss.api_url)
            ss.api_url_for_n8n = st.text_input("API URL as seen by n8n", ss.api_url_for_n8n,
                                               help="Keep 127.0.0.1 (n8n resolves 'localhost' to IPv6). Use http://host.docker.internal:8000 if n8n runs in Docker")
            ss.n8n_url = st.text_input("n8n investigation webhook", ss.n8n_url)
            ss.n8n_decision_url = st.text_input("n8n decision webhook", ss.n8n_decision_url)


# ----------------------------------------------------------------------------- pages
def page_inbox() -> None:
    st.title("🚨 Alert Inbox")
    st.caption("Alerts raised by the bank's rules engine (FRM). Select one and launch the agentic investigation.")
    alerts = api("GET", "/alerts")
    df = pd.DataFrame(alerts)
    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(f'<div class="kpi">{len(df)}</div><div class="muted">alerts</div>', unsafe_allow_html=True)
    c2.markdown(f'<div class="kpi">{(df.status == "NEW").sum()}</div><div class="muted">not yet investigated</div>', unsafe_allow_html=True)
    c3.markdown(f'<div class="kpi">{df.status.isin(["PENDING_REVIEW", "ESCALATED", "EXCEPTION"]).sum()}</div>'
                f'<div class="muted">waiting for a human</div>', unsafe_allow_html=True)
    c4.markdown(f'<div class="kpi">{df.status.str.startswith("CLOSED").sum()}</div><div class="muted">closed</div>',
                unsafe_allow_html=True)
    view = df[["alert_id", "title", "channel", "amount", "frm_score", "trigger_rules", "segment", "status"]].copy()
    view["trigger_rules"] = view["trigger_rules"].apply(lambda r: ", ".join(r))
    view["amount"] = view["amount"].apply(lambda a: f"₹{a:,.0f}" if a else "")
    st.dataframe(view, hide_index=True, use_container_width=True,
                 column_config={"frm_score": st.column_config.ProgressColumn("FRM score", min_value=0, max_value=100, format="%d")})

    st.subheader("Investigate")
    col_a, col_b = st.columns([2, 1])
    with col_a:
        alert_id = st.selectbox("Alert", df.alert_id.tolist(),
                                format_func=lambda a: f"{a} — {df.set_index('alert_id').loc[a, 'title']}")
    with col_b:
        st.write("")
        st.write("")
        go = st.button(f"▶ Run investigation via {'n8n' if st.session_state.orchestrator == 'n8n' else 'Python engine'}",
                       type="primary", use_container_width=True)
    if go:
        with st.status(f"Orchestrating agents with **{st.session_state.model}** …", expanded=True) as status:
            st.write("Fraud Alert → Orchestrator → Transaction Analysis → Customer Behaviour → Risk & Policy → "
                     "Recommendation → Routing policy")
            res = run_investigation(alert_id, st.session_state.model)
            if res.get("ok") is False and not res.get("case_id"):
                status.update(label="Investigation failed", state="error")
                st.error(res.get("error"))
                return
            status.update(label=f"Done — {res.get('case_id')}", state="complete")
        st.session_state.open_case = res.get("case_id")
        render_result_card(res)
        if res.get("case_id"):
            st.info("Open **Cases & Decisions** to review the full agent trace and record the human decision.")

    with st.expander("Run every NEW alert (batch demo)"):
        st.caption("Useful for filling the queues before a demo.")
        if st.button("Run all new alerts"):
            new = df[df.status == "NEW"].alert_id.tolist()
            prog = st.progress(0.0)
            for i, a in enumerate(new):
                run_investigation(a, st.session_state.model)
                prog.progress((i + 1) / max(1, len(new)), text=f"{a} done")
            st.success(f"{len(new)} alerts investigated.")


def render_result_card(res: dict) -> None:
    if not res:
        return
    html = (f'<div class="card"><b>{res.get("case_id", "")}</b> &nbsp; {route_badge(res.get("route"))}'
            f'{band_badge(res.get("risk_band"), res.get("risk_score"))}'
            + (badge(f"AI: {res['decision']}", "#333", "#f1f1f1") if res.get("decision") else "")
            + (badge("QA sample", "#4a4f8c", "#e7e8f7") if res.get("qa_sampled") else "")
            + f'<div class="muted" style="margin-top:6px">{res.get("reason") or res.get("error") or ""}</div></div>')
    st.markdown(html, unsafe_allow_html=True)
    if res.get("rationale"):
        st.markdown(f"**Rationale:** {res['rationale']}")
    if res.get("flags"):
        st.warning("Guardrail flags: " + ", ".join(res["flags"]))


def render_agent(name: str, title: str, res: dict | None) -> None:
    if not res:
        st.markdown(f'<div class="card"><b>{title}</b> <span class="muted">— not run</span></div>', unsafe_allow_html=True)
        return
    ok = res.get("ok")
    g = res.get("grounding") or {}
    head = (f"**{title}** · {'✅ valid' if ok else '❌ failed'} · {res.get('latency_ms', 0)/1000:.1f}s · "
            f"attempts {res.get('attempts')} · {'grounded' if g.get('grounded') else '⚠️ grounding issue'}")
    with st.expander(head, expanded=name == "recommendation"):
        if not ok:
            st.error(res.get("error"))
            if res.get("raw"):
                st.code(res["raw"][:1500])
            return
        out = res["output"]
        if name == "transaction_analysis":
            st.metric("Anomaly score", out["anomaly_score"])
            if out["anomalies"]:
                st.markdown("**Anomalies**")
                st.dataframe(pd.DataFrame(out["anomalies"]), hide_index=True, use_container_width=True)
            if out["mitigating_factors"]:
                st.markdown("**Mitigating factors**")
                st.dataframe(pd.DataFrame(out["mitigating_factors"]), hide_index=True, use_container_width=True)
        elif name == "customer_behaviour":
            c1, c2 = st.columns(2)
            c1.metric("Behaviour risk", out["behaviour_risk"])
            c2.metric("Consistent with profile", "Yes" if out["consistent_with_profile"] else "No")
            for k, lbl in (("account_takeover_indicators", "Account-takeover indicators"),
                           ("social_engineering_indicators", "Scam / social-engineering indicators"),
                           ("mule_indicators", "Mule indicators")):
                if out[k]:
                    st.markdown(f"**{lbl}:** " + "; ".join(out[k]))
        elif name == "risk_policy":
            c1, c2, c3 = st.columns(3)
            c1.metric("Model risk score", out["risk_score"])
            c2.metric("Band", out["risk_band"])
            c3.metric("Typology", out["fraud_typology"].replace("_", " ").title())
            if out["policy_flags"]:
                st.markdown("**Policy / rule flags:** " + ", ".join(out["policy_flags"]))
            if out["regulatory_notes"]:
                st.markdown("**Regulatory notes:** " + "; ".join(out["regulatory_notes"]))
        else:
            c1, c2 = st.columns(2)
            c1.metric("Recommendation", out["decision"])
            c2.metric("Confidence", f"{out['confidence']:.0%}")
            st.markdown(f"**Rationale:** {out['rationale']}")
            st.markdown("**Recommended actions:** " + "; ".join(out["recommended_actions"]))
            if out.get("customer_message"):
                st.markdown(f"**Draft customer message:** _{out['customer_message']}_")
        if "summary" in out:
            st.caption(out["summary"])
        st.caption(f"Evidence cited: {', '.join(sorted(set(_ids(out)))) or '—'}"
                   + (f" · invalid citations: {g['invalid_citations']}" if g.get("invalid_citations") else "")
                   + (f" · unsupported claims: {g['unsupported_claims']}" if g.get("unsupported_claims") else ""))
        with st.popover("Raw JSON"):
            st.json(out)


def _ids(data) -> list[str]:
    out = []
    if isinstance(data, dict):
        for k, v in data.items():
            out += v if k == "evidence_ids" and isinstance(v, list) else _ids(v)
    elif isinstance(data, list):
        for x in data:
            out += _ids(x)
    return out


def page_cases() -> None:
    st.title("⚖️ Cases & Decisions")
    st.caption("Human-in-the-loop workspace: review the agents' work and approve, override or escalate.")
    cases = api("GET", "/cases")
    if not cases:
        st.info("No cases yet — run an investigation from the Alert Inbox.")
        return
    df = pd.DataFrame(cases)
    queue = st.radio("Queue", ["Needs a decision", "Escalated (L2)", "Exceptions", "QA samples (auto-closed)", "All cases"],
                     horizontal=True)
    if queue == "Needs a decision":
        sel = df[df.status.isin(["PENDING_REVIEW", "ESCALATED", "EXCEPTION"])]
    elif queue == "Escalated (L2)":
        sel = df[df.status == "ESCALATED"]
    elif queue == "Exceptions":
        sel = df[df.status == "EXCEPTION"]
    elif queue == "QA samples (auto-closed)":
        sel = df[(df.status == "AUTO_CLOSED") & (df.qa_sampled)]
    else:
        sel = df
    st.dataframe(sel[["case_id", "alert_id", "model", "orchestrator", "status", "route", "risk_score", "decision",
                      "confidence", "total_latency_ms"]], hide_index=True, use_container_width=True)
    if sel.empty:
        st.success("Queue is empty.")
        return
    ids = sel.case_id.tolist()
    default = ids.index(st.session_state.get("open_case")) if st.session_state.get("open_case") in ids else 0
    case_id = st.selectbox("Open case", ids, index=default)
    render_case(case_id)


def render_case(case_id: str) -> None:
    c = api("GET", f"/cases/{case_id}")
    g = c.get("guardrails") or {}
    ev = c.get("evidence") or {}
    st.markdown(f'### {case_id} &nbsp; {route_badge(c.get("route"))}{band_badge(c.get("risk_band"), c.get("risk_score"))}'
                + (badge(f"AI recommends {c['decision']}", "#333", "#f1f1f1") if c.get("decision") else "")
                + (badge(f"Human: {c['human_decision']} ({c['decided_by']})", "#1f4e8c", "#e2ecf8") if c.get("human_decision") else ""),
                unsafe_allow_html=True)
    st.caption(f"Alert {c['alert_id']} · {ev.get('alert_title', '')} · model {c['model_label']} · via {c['orchestrator']} · "
               f"status {c['status']}")
    if g.get("reason"):
        st.markdown(f"**Routing policy:** {g['reason']}")
    if g.get("interventions"):
        st.warning("Guardrail interventions: " + "; ".join(g["interventions"]))
    if g.get("flags"):
        st.warning("Flags: " + ", ".join(g["flags"]))

    t1, t2, t3, t4 = st.tabs(["🤖 Agent trace", "📂 Evidence pack", "🛡️ Guardrails", "📜 Audit trail"])
    with t1:
        for a in c["agent_order"]:
            render_agent(a, c["agent_titles"][a], (c.get("agents") or {}).get(a))
    with t2:
        if not ev:
            st.error(c.get("error") or "Evidence unavailable")
        else:
            st.markdown(f"**Rules engine:** score {ev['rule_score']} ({ev['rule_band']})")
            st.dataframe(pd.DataFrame(ev["rule_hits"])[["rule_id", "name", "severity", "weight", "evidence"]]
                         if ev["rule_hits"] else pd.DataFrame(), hide_index=True, use_container_width=True)
            st.markdown("**Facts sent to the agents (pseudonymised)**")
            for f in ev["facts"]:
                st.markdown(f'<div class="fact"><b>{f["id"]}</b> [{f["source"]}] {f["text"]}</div>', unsafe_allow_html=True)
            st.markdown("**Untrusted remarks (after injection screen)**")
            st.code(ev.get("untrusted_remarks") or "(none)")
            if ev.get("injection_detected"):
                st.error("Prompt-injection screen removed instruction-like text from the remarks before any model saw it.")
    with t3:
        st.json(g or {})
        rows = []
        for a, r in (c.get("agents") or {}).items():
            if r:
                rows.append({"agent": a, "valid_first_try": r.get("valid_first_try"), "attempts": r.get("attempts"),
                             "grounded": (r.get("grounding") or {}).get("grounded"),
                             "unsupported_claims": ", ".join((r.get("grounding") or {}).get("unsupported_claims", [])),
                             "pii_leak_blocked": r.get("pii_leak_blocked"), "prompt_version": r.get("prompt_version")})
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    with t4:
        audit = pd.DataFrame(c.get("audit", []))
        if not audit.empty:
            st.dataframe(audit, hide_index=True, use_container_width=True)
        if c.get("actions"):
            st.markdown("**Actions**")
            st.dataframe(pd.DataFrame(c["actions"]), hide_index=True, use_container_width=True)

    decidable = c["status"] in ("PENDING_REVIEW", "ESCALATED", "EXCEPTION") or (c["status"] == "AUTO_CLOSED" and c.get("qa_sampled"))
    st.divider()
    if not decidable:
        st.caption("No human decision is pending on this case.")
        return
    st.subheader("Investigator decision")
    role = st.session_state.role
    if role == "AUDITOR":
        st.info("Auditors have read-only access.")
        return
    if c.get("route") == "E" and role != "SENIOR":
        st.warning("Escalated cases can only be decided by a Senior Investigator (L2). Switch role in the sidebar.")
    options = ["PROCEED", "VERIFY", "HOLD", "ESCALATE"]
    ai = c.get("decision")
    with st.form(f"decide-{case_id}"):
        decision = st.radio("Decision", options, index=options.index(ai) if ai in options else 1, horizontal=True,
                            format_func=lambda d: f"{d}{' (AI)' if d == ai else ''}")
        st.caption(DECISION_HELP[decision])
        reason = st.text_area("Reason / notes (mandatory when overriding the AI or deciding an exception)")
        submitted = st.form_submit_button("Submit decision", type="primary")
    if submitted:
        res = submit_decision(case_id, decision, reason)
        if not res.get("ok"):
            st.error(res.get("error") or res)
        else:
            st.success(res.get("message") or f"Decision recorded: {res.get('human_decision')} · override={res.get('override')}")
            if res.get("actions_executed"):
                st.markdown("**Executed (simulated):** " + ", ".join(res["actions_executed"]))
            if res.get("actions_withheld"):
                st.warning("Withheld (needs L2): " + ", ".join(res["actions_withheld"]))
            if res.get("outcome"):
                st.info(res["outcome"])
            if res.get("str_draft"):
                st.code(res["str_draft"])


def page_models() -> None:
    st.title("📊 Model Comparison")
    st.caption("Same 13 cases, same prompts, same evidence and output schema for every open-weight model.")
    res_dir = ROOT / "benchmark" / "results"
    summary_file = res_dir / "summary.json"
    if not summary_file.exists():
        st.info("No benchmark results yet. Run `python -m benchmark.run_benchmark` (see README) and refresh.")
        return
    data = json.loads(summary_file.read_text(encoding="utf-8"))
    df = pd.DataFrame(data["models"])
    st.caption(f"Run {data.get('run_id')} · prompt {data.get('prompt_version')} · {data.get('cases')} cases")
    show = ["model", "open_weight", "decision_accuracy", "acceptable_accuracy", "route_accuracy", "escalation_recall",
            "escalation_precision", "false_positive_rate", "fraud_recall", "hallucination_rate",
            "explanation_quality", "schema_first_try", "schema_final", "task_completion", "avg_latency_s",
            "p95_latency_s", "composite_score"]
    st.dataframe(df[[c for c in show if c in df.columns]], hide_index=True, use_container_width=True)
    metric = st.selectbox("Chart metric", [c for c in show[1:] if c in df.columns], index=0)
    st.bar_chart(df.set_index("model")[metric])
    charts = sorted((res_dir / "charts").glob("*.png")) if (res_dir / "charts").exists() else []
    if charts:
        cols = st.columns(2)
        for i, p in enumerate(charts):
            cols[i % 2].image(str(p), caption=p.stem.replace("_", " "))
    per_case = res_dir / "per_case.csv"
    if per_case.exists():
        st.subheader("Per-case decisions")
        pc = pd.read_csv(per_case)
        pivot = pc.pivot_table(index=["alert_id", "expected_decision"], columns="model", values="decision", aggfunc="first")
        st.dataframe(pivot, use_container_width=True)


def page_governance() -> None:
    st.title("🛡️ Governance & Audit")
    v = api("GET", "/audit/verify")
    c1, c2, c3 = st.columns(3)
    c1.metric("Audit entries", v["entries"])
    c2.metric("Hash chain", "Intact ✅" if v["valid"] else f"BROKEN at {v['broken_at_seq']} ❌")
    h = api("GET", "/health")["thresholds"]
    c3.metric("Auto-close limit", f"risk < {h['auto_close_max_risk']}, conf ≥ {h['auto_close_min_confidence']}")
    st.markdown(f"Escalation threshold: risk ≥ **{h['escalate_min_risk']}** · QA sampling of autonomous closures: "
                f"**{h['qa_sample_rate']:.0%}**")
    roles = api("GET", "/roles")
    st.subheader("Action guardrails (who may do what)")
    st.dataframe(pd.DataFrame([{"action": k, "label": a["label"], "autonomous": a["autonomous"],
                                "allowed roles": ", ".join(a["roles"]), "reversible": a["reversible"]}
                               for k, a in roles["actions"].items()]), hide_index=True, use_container_width=True)
    st.subheader("Notifications")
    st.dataframe(pd.DataFrame(api("GET", "/notifications")), hide_index=True, use_container_width=True)
    st.subheader("Audit log (latest first)")
    st.dataframe(pd.DataFrame(api("GET", "/audit")), hide_index=True, use_container_width=True)
    st.subheader("Fraud rule catalogue")
    st.dataframe(pd.DataFrame(api("GET", "/rules")), hide_index=True, use_container_width=True)
    with st.expander("Demo controls"):
        if st.button("Reset cases & audit (keep synthetic bank)"):
            api("POST", "/admin/reset")
            st.success("Reset done.")


def page_about() -> None:
    st.title("🏗️ Architecture")
    st.markdown("""
**Flow:** Fraud Alert → n8n Orchestrator → Transaction Analysis Agent → Customer Behaviour Agent → Risk & Policy Agent →
Recommendation Agent → deterministic Routing Policy → **[A]** auto-close / **[H]** investigator / **[E]** L2 escalation /
**[X]** exception → Human decision → Outcome actions (simulated) → hash-chained audit trail.
""")
    for p in sorted((ROOT / "docs" / "diagrams").glob("*.png")):
        st.image(str(p), caption=p.stem.replace("_", " "))


sidebar()
pg = st.navigation([
    st.Page(page_inbox, title="Alert Inbox", icon="🚨", default=True, url_path="inbox"),
    st.Page(page_cases, title="Cases & Decisions", icon="⚖️", url_path="cases"),
    st.Page(page_models, title="Model Comparison", icon="📊", url_path="models"),
    st.Page(page_governance, title="Governance & Audit", icon="🛡️", url_path="governance"),
    st.Page(page_about, title="Architecture", icon="🏗️", url_path="architecture"),
])
pg.run()
