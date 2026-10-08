# Team Study Guide: what every member must be able to explain

## A. The one-minute pitch
Fraud alerts are high-volume, mostly false positives, and time-critical. We put four specialised AI agents *after* the bank's
rules engine. They gather and reason over the evidence in seconds. A deterministic routing policy auto-closes only clear false
positives, sends ambiguous cases to an analyst and escalates high-risk ones to a senior investigator. Humans make every adverse
decision, and every step is audit-logged. We compared three open-weight models under identical conditions.

## B. Follow one case through the system (ALT-3001)
1. **UI** (`page_inbox`) posts `{alert_id, model}` to the **n8n** webhook `/fraud-investigate`.
2. n8n *Config* → *Open Case* (`POST /cases/start`, which creates `CASE-3001-xxxx` and an audit entry).
3. *Gather Evidence* (`POST /tools/evidence` → `tools.build_evidence_pack`):
   - queries transactions, customer, account, device, beneficiary, security events, watchlist and prior alerts;
   - computes features (amount 237× baseline, drain 96%, device age 1 h, SIM swap…);
   - runs 15 rules (score 100);
   - pseudonymises names and accounts, screens the remarks;
   - stores ~18 facts E1…E18.
4. IF *Evidence complete?* → yes.
5. *Agent 1–4* (`POST /agents/{name}` → `agents.run_agent`), for each agent:
   - builds the STOIC prompt and checks for PII leaks;
   - calls the model (temperature 0, JSON mode) and parses the JSON;
   - validates against the Pydantic schema (one repair retry);
   - runs the grounding check; results are stored and audited.
6. *Routing Policy* (`POST /guardrails/route` → `guardrails.decide_route`):
   - final risk = max(model risk, rules score) = 100, plus a CRITICAL rule (mule watchlist), so route **E**.
7. Switch → *[E] Escalate* (`POST /cases/{id}/apply/E`): L2 notified, alert status ESCALATED.
8. L1 tries to decide → WF-02 → `/cases/decision` → **403**. L2 approves ESCALATE → actions `temporary_hold`, `block_card_or_channel`, `draft_str_for_compliance`, `advise_cybercrime_1930` (simulated), plus an STR draft.
9. Every step is in `audit_log`, with each row's hash covering the previous hash.

## C. Likely viva questions and model answers
1. **Why not let the AI decide?** Adverse actions affect customers' money and carry regulatory accountability (RBI FREE-AI: people first, accountability). LLMs can hallucinate or be manipulated. So agents recommend, and a deterministic policy plus humans decide. Only reversible, low-risk false-positive closures are autonomous, and 10% of those are QA-sampled.
2. **What exactly is "agentic" here?** Specialised agents with distinct goals act in sequence on tool-gathered evidence. Each agent builds on the previous agents' outputs, and an orchestrator routes conditionally. The agents use tools (evidence builder, rules engine, watchlists) through the orchestrated workflow.
3. **Why four agents instead of one big prompt?** Separation of concerns: smaller prompts, a schema per role, and failures traceable to one step. It also matches how investigation is actually done (transaction → customer → risk/policy → decision).
4. **What is the routing policy?** E if ESCALATE, risk ≥ 70 or a CRITICAL rule. A only if PROCEED, risk < 30, confidence ≥ 0.8, no flags and no HIGH rule. X if data is missing or an agent fails. Otherwise H. It is in `guardrails.decide_route`.
5. **What is the "risk floor"?** Final risk = max(model's risk, rules-engine score). A model, or an injected instruction, can't talk a case below what the bank's own rules computed.
6. **How do you detect hallucination?**
   - Every fact has an ID, and agents must cite IDs; citations to non-existent IDs are flagged.
   - Risk claims like "SIM swap" or "watchlist" are checked against the computed signals, with negation handling.
   - Flagged cases can't auto-close. It's keyword-based, so it's a guardrail, not a proof.
7. **How do you stop prompt injection?** Remarks are screened with patterns; instruction-like sentences are removed and the case is flagged. Prompts declare remarks as untrusted data. The risk floor still applies. The benchmark stress test shows what happens with the screen off.
8. **How is PII protected?** Synthetic data, pseudonymised names and accounts (`[NAME]`, `XXXX1234`), age *band* instead of age, and a PII-leak check on every prompt before it leaves.
9. **What is STOIC?** Situation, Task, Objective, Information, Constraints. Every agent prompt uses it, and v1 → v3 shows the refinement (`prompt_evolution.py` gives the evidence).
10. **Why n8n?** Visual, auditable orchestration with webhooks, IF/Switch routing, error outputs and schedules. Non-developers and auditors can read the flow.
11. **Why also a built-in (Python) engine?** It runs the same step functions as n8n, so it is the fallback if n8n is down, it runs the benchmark, and it is the fast daily mode: agents 1 and 2 run in parallel because they only read the evidence. n8n keeps them in sequence with a validity check after each, which is easier to show and audit.
11b. **Why did you move off Streamlit?** Streamlit re-runs the whole script on every click, so the screen lagged. The light web app is one HTML page served by the API: each click calls one endpoint, and investigation progress is polled so each agent appears as it finishes. Same functions, much less work per click.
12. **Which models and why?** Open-weight Gemma 4 31B (Google, via Google AI Studio), gpt-oss 120B (OpenAI) and Qwen 3.8 27B (Alibaba), both via Cerebras: three vendors, all on free tiers. Gemini is closed, so it is only an optional reference. Ollama is the local option. They are interchangeable via an OpenAI-compatible gateway.
13. **How did you make the comparison fair?** Same cases, evidence, prompts, schema, policy and thresholds; temperature 0; latency excluding rate-limit waits; ground truth hidden from agents.
14. **What do your metrics mean?** See `docs/03…` 3.3. Know the difference between decision accuracy (model only) and route accuracy (model + guardrails), and what "guardrail saves" counts.
15. **What's the false-positive rate here?** Genuine cases (7) that were recommended HOLD/ESCALATE or routed E, divided by 7.
16. **What happens if the model returns bad JSON?** Tolerant parsing, then one repair prompt, then route X with ops notified. The case is never silently dropped.
17. **What if an API call fails inside n8n?** The node's error output goes to *Technical Exception*, which returns a clean error to the UI (route X).
18. **How is access controlled?** Roles are L1, L2 and Auditor. E-route cases and blocking actions are L2 only. Overrides need ≥ 15 characters of reason. Auditor is read-only.
19. **How do you prove the audit log wasn't altered?** Each row's SHA-256 includes the previous row's hash; `/audit/verify` recomputes the chain. The test edits a row and the check fails.
20. **Which framework did you map to and why?** NIST AI RMF as primary (risk-based: Govern/Map/Measure/Manage), cross-referenced to ISO/IEC 42001 controls and RBI FREE-AI for Indian banking expectations.
21. **Bias concerns?** No names, gender or religion in prompts; age band used only protectively for scam vulnerability. The dataset is too small for statistical fairness testing, so we recommend segment-level FPR monitoring in a pilot.
22. **What's synthetic about the data?** Everything. Generated by `seed_data.py` with a fixed seed: 12 customers, ~1,800 transactions and 13 alerts designed to cover normal, ambiguous, exception and high-risk typologies.
23. **What are the limitations?** Small test set, simulated actions, keyword-based grounding check, rubric-based explanation score, free-tier rate limits.
24. **How would a bank deploy this?** Self-host the open model (data stays in the bank), connect real FRM/CBS via APIs, run shadow mode on historical alerts, calibrate thresholds with the risk committee, then a phased roll-out with QA sampling.
25. **What did the expert change?** *(Fill in after the interview.)*

## D. Key numbers to remember
13 cases (4 normal, 4 ambiguous, 1 exception, 4 high-risk) · 15 rules · 4 agents · 3 n8n workflows · thresholds 30 / 0.8 / 70 ·
10% QA sample · 15-minute SLA · 30 automated tests · prompt v3.1 · agents 1 & 2 in parallel (built-in engine).
