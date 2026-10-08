# Final Demonstration Video: Script (target 13–14 min; allowed 10–15)

Written for five members (M1–M5). With fewer members, merge segments; every member must appear meaningfully.
Record the screen at 1080p with each speaker's webcam in a corner. Before recording:
- Start with launcher option **9** (API + n8n; the app opens with Orchestrator = n8n workflow).
- Reset the demo (Governance & audit → *Reset demo cases*, click twice).
- Select a real model (e.g. `gemma-4-31b`) and check Orchestrator = **n8n workflow (running)**.
- Keep the n8n canvas open in a second tab.

| # | Time | Speaker | On screen | Script (key lines) |
|---|---|---|---|---|
| 1 | 0:00–1:00 | M1 | Title slide / website hero | "Banks get thousands of fraud alerts. Most are false positives, yet real frauds move money in minutes. We built FraudOps Copilot, a governed agentic AI that investigates alerts in seconds while humans keep decision authority." |
| 2 | 1:00–2:30 | M1 | Stage 1 page: 5 Whys, causal-loop diagram, idea screening | "Critical thinking showed the bottleneck is investigation, not detection… Systems thinking: the fatigue spiral and three leverage points… STOIC prompts v1→v3 and why each change was needed." |
| 3 | 2:30–3:45 | M2 | AS-IS → TO-BE diagrams | "Today: 6–8 systems, swivel-chair look-ups, duplicate L2 effort. TO-BE: evidence tool and four agents run autonomously [A]; analysts decide [H]; high risk escalates to L2 with an SLA [E]; data gaps go to exceptions." |
| 4 | 3:45–4:45 | M2 | Architecture diagram | "Seven layers: web UI, n8n, agents, open-weight models, tools and data, guardrails, human oversight. Agents only recommend; a deterministic policy routes." |
| 5 | 4:45–6:15 | M3 | **Live**: Alert inbox → *Investigate* ALT-1001 via n8n (each agent ticks off live); switch to the n8n *Executions* tab | "The UI calls the n8n webhook… evidence, four agents, routing… Auto-closed: card present, home city, earlier identical false positive. 10% of these are QA-sampled." |
| 6 | 6:15–7:45 | M3 | **Live**: ALT-3001 → *Open case*: agent trace, evidence pack | "SIM swap, password reset, emulator, mule-watchlist payee, 96% balance drain: escalated. As an L1 analyst I'm blocked [show error]. Switching to L2: approve ESCALATE. Protective actions run and an STR draft goes to compliance." |
| 7 | 7:45–8:45 | M4 | **Live**: ALT-3004 (injection) and ALT-2003 (exception); ALT-2001 override with reason | "The remarks say 'ignore all previous instructions…'. The screen removed it before any model saw it and flagged the attempt. The missing customer record goes to the exception queue. Overrides need a written reason." |
| 8 | 8:45–10:15 | M4 | Model comparison page + charts | "Three open-weight models, same 13 cases, prompts and policy. [State real results: best accuracy / escalation recall, lowest FPR, fastest, schema failures, any model fooled in the injection stress test.] We recommend ___ because…" |
| 9 | 10:15–11:30 | M5 | Guardrails mapping table; Governance & audit page: audit-chain check | "Fourteen risks mapped Risk → Guardrail → Requirement → Implementation against NIST AI RMF, with ISO 42001 and RBI FREE-AI. Every step is hash-chained; here's the tamper check." |
| 10 | 11:30–12:45 | M5 | Expert slide: photo/name (with consent), 2–3 quotes | "We interviewed [Name, Designation, Org]. They validated ___, challenged ___, and we changed ___." |
| 11 | 12:45–13:45 | M1 + all | Limitations + next steps; team on camera | "Limitations: synthetic data, 13 cases, simulated actions. Next: pilot on historical alerts, segment-level fairness monitoring, rule-tuning agent. Thank you." |

**Checklist:**
- Every member speaks on camera.
- No API keys visible on screen (close `.env`).
- Real model results stated, not the mock.
- Duration between 10:00 and 15:00.
- Upload unlisted to YouTube or Drive and embed the link on the website.
