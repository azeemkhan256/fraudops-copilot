# Evidence Log: numbered & captioned screenshots

Save captures in `docs/screenshots/` with the file name shown, then paste them into the report in this order.
Caption format: **Figure N - <caption>**. Reference captures from the build environment (offline mock model) are in
`docs/screenshots/reference/`. **Re-capture all of them on your machine with a real model.**

| Fig | File | What to capture | Caption |
|---|---|---|---|
| 1 | `01_n8n_wf01_canvas.png` | n8n editor, WF-01 full canvas, *Published* visible | n8n Investigation Orchestrator: webhook → evidence → four agents → routing policy → A/H/E/X |
| 2 | `02_n8n_wf01_execution_success.png` | n8n *Executions* tab, one successful run, node outputs visible | Successful n8n execution of ALT-3001 showing each agent's output |
| 3 | `03_n8n_wf01_exception_path.png` | Execution where the evidence IF took the false branch (ALT-2003) | Exception path: customer master data unavailable → route X |
| 4 | `04_n8n_wf02_canvas.png` | WF-02 canvas | HITL Decision Handler with role validation and outcome branches |
| 5 | `05_n8n_wf02_rejected.png` | Execution where L1 was rejected (403) | Role-based control: L1 cannot decide an escalated case |
| 6 | `06_n8n_wf03_sla.png` | WF-03 canvas + one execution | SLA escalation monitor (every 5 minutes) |
| 7 | `07_ui_alert_inbox.png` | Alert inbox with KPIs and the 13 alerts, plus one run card mid-investigation (agents ticking off) | Alert inbox: FRM alerts and a live investigation |
| 8 | `08_ui_run_auto_close.png` | Finished run card for ALT-1001 | ALT-1001 auto-closed [A]: risk, confidence, rationale |
| 9 | `09_ui_agent_trace.png` | Case queues → ALT-3001 → Agent trace tab | Four-agent trace with latencies, validity and grounding |
| 10 | `10_ui_evidence_pack.png` | Evidence-pack tab (facts E1…En, rule hits, pseudonymised values) | Evidence pack sent to the models: pseudonymised, citation-ready |
| 11 | `11_ui_hitl_blocked_l1.png` | L1 analyst attempting ESCALATE on ALT-3001 → error | Access control: escalations require a Senior Investigator |
| 12 | `12_ui_hitl_l2_approved.png` | L2 approval → executed actions + STR draft | L2 decision: protective actions executed and STR draft generated |
| 13 | `13_ui_override_reason.png` | ALT-2001 override without reason (error) then with reason | Overrides require a written reason |
| 14 | `14_ui_prompt_injection.png` | ALT-3004 evidence tab showing the removed text and flag | Prompt-injection screen quarantined instructions in payment remarks |
| 15 | `15_ui_exception_case.png` | ALT-2003 in the Exceptions queue | Data exception routed to manual handling |
| 16 | `16_ui_governance_audit.png` | Governance & audit page: hash chain intact, action matrix | Governance & audit: tamper-evident log and action guardrails |
| 17 | `17_model_comparison_table.png` | Model comparison page: metrics table | Open-model comparison on 13 identical cases |
| 18–22 | `18…22_chart_*.png` | `benchmark/results/charts/1…5` | Classification quality · error rates · reliability · latency · composite |
| 23 | `23_per_case_matrix.png` | Per-case decisions pivot | Per-case decisions by model vs expected |
| 24 | `24_prompt_evolution.png` | `prompt_evolution.md` table | Prompt refinement v1 → v3: structure, citations, injection resistance |
| 25 | `25_tests_passing.png` | Terminal: `pytest -q` → 30 passed (launcher option 5) | Automated test suite |
| 26 | `26_api_docs.png` | <http://localhost:8000/docs> | FastAPI endpoints used by n8n |

**Prompts evidence:** include `app/prompts.py` (v3.1 system prompt for one agent) and `prompt_evolution.md` in the appendix.
**Model outputs evidence:** `benchmark/results/per_case.csv` and two raw JSON outputs from `raw_runs.jsonl`.
