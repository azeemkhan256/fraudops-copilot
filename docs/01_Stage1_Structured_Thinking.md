# Stage 1: Structured Thinking, STOIC Prompt Engineering & Research

> **Team note:** Replace framework names with the exact terms used in your course book where they differ.
> STOIC is expanded here as **S**ituation, **T**ask, **O**bjective, **I**nformation (inputs), **C**onstraints.
> If your class used a different expansion, keep the structure and rename the letters.

## 1.1 Problem in one paragraph
Indian banks run rules-based Fraud Risk Management (FRM) engines that raise alerts on suspicious digital transactions.
Each alert is triaged by an L1 fraud analyst. The analyst opens several systems (core banking, card switch, device and
login logs, beneficiary records, watchlists, CRM), pieces the evidence together and decides whether to release,
verify, hold or escalate. Most alerts turn out to be genuine customer activity. Meanwhile, real frauds such as account
takeover, mule pass-through and authorised-push-payment scams move money out within minutes. The challenge is to
redesign this investigation with **governed Agentic AI**: agents gather and reason over the evidence, and humans keep
decision authority.

Scale (RBI Annual Report 2024-25):
- 23,953 bank-fraud cases were reported in FY25, involving ₹36,014 crore.
- Digital-payment (card/internet) frauds were 13,516 cases, 56.5% of all cases, with ₹520 crore involved.

So digital fraud is the high-volume, low-ticket problem that analysts face every day. The jump in the total amount was
mainly due to 122 older cases (₹18,674 crore) being re-reported after a Supreme Court judgment.

## 1.2 Critical Thinking analysis
We used the *elements of thought*: purpose, question, information, assumptions, concepts, inferences, implications
and point of view. We then challenged our own first framing.

| Element | Our analysis |
|---|---|
| **Purpose** | Cut time-to-decision and analyst workload on fraud alerts without increasing missed fraud or harming genuine customers |
| **Key question** | Which parts of an investigation can an AI agent do *safely and verifiably*, and which must stay with a human? |
| **Information needed** | Transaction and 90-day baseline, customer profile, device and security events, beneficiary age/history, watchlists, rule hits, prior alert dispositions, bank policy |
| **Assumptions challenged** | (1) "AI should decide fraud": rejected. Accountability and RBI expectations require human authority for adverse actions. (2) "More rules = more safety": rejected. Rules raise false positives and analyst fatigue. (3) "LLMs are reliable if prompted well": rejected. Outputs must be schema-validated and grounding-checked. |
| **Concepts** | Alert triage, false positive, account takeover (ATO), money mule, authorised-push-payment (APP) scam, step-up verification, maker-checker / four-eyes, explainability |
| **Inferences** | Most effort is *evidence gathering and summarising*, which is ideal for agents. The *decision* carries legal and customer impact and stays human, except for clear false positives under strict thresholds |
| **Implications** | Needs deterministic guardrails, audit trail, role-based approval, QA sampling of autonomous closures, and a model comparison before adoption |
| **Point of view** | Considered: customer (no unnecessary blocks), analyst (less swivel-chair work), L2/compliance (defensible decisions), regulator (accountability, fairness), bank (loss and cost) |

**5 Whys** (why do frauds still succeed despite alerts?)
1. Money leaves before the alert is actioned.
2. Analysts are queued behind many low-risk alerts.
3. Each alert takes many minutes of manual look-ups across systems.
4. The evidence sits in separate systems with no consolidated case view.
5. FRM tools *detect* but don't *investigate*.

**Root cause:** the bottleneck is investigation, not detection. Our solution therefore sits *after* the rules engine and
does not replace it.

## 1.3 Systems Thinking analysis
**System boundary.** In scope: from "FRM alert raised" to "disposition recorded and action executed".
Out of scope: rule authoring, chargebacks, legal recovery.

**Actors and parts:**
- **Bank people:** customer, FRM engine, L1 analyst, L2 senior investigator, fraud-ops head, compliance/AML (STR filing to FIU-IND), contact centre.
- **Systems:** core banking (CBS), card switch, mobile/net-banking, device intelligence, telco SIM-swap signals.
- **External:** mule-account intelligence (e.g. RBIH MuleHunter.AI), cyber-crime helpline 1930 / cybercrime.gov.in.

**Iceberg model**

| Level | Observation |
|---|---|
| Events | A ₹4.8 lakh IMPS to a new payee at 2 a.m. is noticed after the money is gone |
| Patterns | Alert backlogs peak at night, on weekends and on salary days; most alerts close as false positives |
| Structures | Siloed systems, static rule thresholds, manual evidence collection, no case-level SLA |
| Mental models | "Every alert must be read by a human"; "a tighter rule is a safer rule" |

**Feedback loops** (causal-loop diagram in `docs/diagrams/00_systems_thinking_loops.png`)
- **R1, the fatigue spiral (reinforcing):** more rules → more alerts → bigger backlog → slower response → more losses → pressure to add rules → more alerts.
- **R2, the friction spiral (reinforcing):** false positives → blocked genuine customers → complaints and customer workarounds → noisier data → more false positives.
- **B1, learning (balancing):** analyst dispositions → rule tuning → fewer false positives. This loop is weak today because dispositions are rarely analysed.

**Leverage points** (highest leverage first):
1. Change *who gathers the evidence*. Agents assemble the case in seconds.
2. Change *the routing rule*. Clear false positives are auto-closed under strict, audited thresholds, so humans see only the alerts that need them.
3. Strengthen B1. Every disposition and override is logged with a reason, ready for rule-tuning analysis.

**Unintended consequences to guard against:**
- Automation bias: investigators rubber-stamping AI recommendations. Mitigated by mandatory reasons for overrides, a visible evidence pack and QA sampling.
- Attackers targeting the AI with prompt injection in payment remarks. Mitigated by the injection screen and by treating remarks as data.
- Silent model drift. Mitigated by benchmark re-runs and prompt versioning.

## 1.4 STOIC prompt engineering

### a) Brainstorming prompt (ideation)
```
S – Situation: I am part of a student team redesigning fraud-alert investigation at an Indian retail bank. L1 analysts
    manually check transactions, customer profile, device/login events, beneficiaries and watchlists before deciding
    to release, verify, hold or escalate. Most alerts are false positives; real frauds move money within minutes.
T – Task: Generate 8 distinct AI/automation solution ideas for this process.
O – Objective: Find the idea with the best mix of usefulness to the bank, realism for a 4-week student PoC, and
    feasibility with open-weight LLMs and synthetic data, while keeping humans in control.
I – Information: Tools available: n8n, Streamlit/Flask, Python, open models (Llama, Qwen, Mistral, Gemma, gpt-oss) via
    Google AI Studio, Cerebras or Ollama. Regulatory context: RBI FREE-AI framework (2025), DPDP Act 2023, PMLA STR reporting.
C – Constraints: Synthetic data only; no autonomous irreversible actions; must show HITL, escalation, exception
    handling; output a table with idea, users, AI role, human role, data needed, main risk.
```

### b) Prompt refinement for the investigation agents
| Version | Prompt (abridged) | Weakness identified in review | Change made in next version |
|---|---|---|---|
| **v1** | "You are a fraud analyst at a bank. Here is a flagged transaction and its context: {facts + raw remarks}. Is this fraud? Explain." | Free text can't drive automated routing; nothing ties claims to evidence; raw customer remarks are mixed into the instructions, so injected text can steer the model | Added the **S-T-O** structure and a fixed JSON reply |
| **v2** | "Situation… Task… Objective… Reply in JSON {decision, risk_score, rationale}" + un-numbered facts + raw remarks | Structured, but claims still can't be traced to sources; no shared risk scale, so scores aren't comparable across models; remarks are still not marked as untrusted | Added **numbered evidence facts (E1…En)** with mandatory citations, a **risk scale**, the **bank policy P1–P7**, and the **I** and **C** blocks (untrusted-data rule, no PII, no actions, only provided facts) |
| **v3** | Full STOIC per agent (see `app/prompts.py`), plus code-side guardrails: injection screen, schema validation with one repair retry, grounding check | Small models may still add prose or use wrong field types | Handled in code: tolerant JSON extraction, schema repair, grounding flags feed the routing policy |
| **v3.1 (current)** | Same STOIC prompts; the Situation now says Transaction Analysis and Customer Behaviour assess the evidence *independently*, and only Risk & Policy and Recommendation receive earlier agents' outputs | Agent 2 used to read agent 1's output, so the two could not run at the same time and agent 2 could simply echo agent 1 | Agents 1 and 2 run in parallel (faster) and give two independent views that Risk & Policy has to reconcile |

**Evidence of refinement:** `python -m benchmark.prompt_evolution --model gemma-4-31b` runs v1, v2 and v3 on the same
three cases (domestic travel, senior-citizen scam, prompt injection). It produces `benchmark/results/prompt_evolution.md`,
a table of valid structured output, decision, evidence IDs cited, unsupported claims and whether the injected
instruction was followed. **Paste that table and two excerpts here.** Every case and benchmark run records
`prompt_version = v3.1`.

## 1.5 Idea generation and screening
Each idea was scored 1–5 on **Usefulness** (U, weight 40%), **Realism** (R, 30%) and **Feasibility** (F, 30%).

| # | Idea | U | R | F | Score | Verdict |
|---|---|---|---|---|---|---|
| 1 | **Agentic alert-investigation copilot with HITL routing** (evidence agents + recommendation + human decision) | 5 | 5 | 4 | **4.7** | **Selected** |
| 2 | Replace rules with an ML transaction-scoring model | 4 | 2 | 2 | 2.8 | Needs large labelled data; not agentic |
| 3 | Payment-time scam-warning chatbot for customers | 4 | 3 | 3 | 3.4 | Valuable, but outside the investigation process |
| 4 | Automated STR narrative drafting for compliance | 3 | 4 | 4 | 3.6 | Included as a sub-feature (STR draft after L2 decision) |
| 5 | Graph analytics for mule-ring detection | 4 | 3 | 2 | 3.1 | Data-hungry; we include a mule-pattern rule and watchlist instead |
| 6 | Rule-tuning agent that learns from dispositions | 4 | 3 | 3 | 3.4 | Future phase (strengthens loop B1) |
| 7 | Voice-bot for customer verification calls | 3 | 2 | 2 | 2.4 | Regulatory and consent complexity |
| 8 | Dispute/chargeback automation | 3 | 4 | 3 | 3.3 | Different process |

## 1.6 Market and existing-solution review
| Solution | What it does | Relevance / gap |
|---|---|---|
| **NICE Actimize X-Sight ActOne / InvestigateAI** (Apr 2025) | Agentic investigation plans built from institutional policies; claims investigation time cut by 50% or more; insights traceable to data | Validates the agentic-investigation approach. Proprietary and enterprise-priced |
| **Feedzai Farol** (Sep 2026) | Embedded agent in RiskOps: alert summaries, rule analysis, SAR drafting. Claims 20% faster alert handling and SAR drafting up to 12× faster. Feedzai research: 68% of FIs are testing agentic AI | Analyst keeps authority, as in our design |
| **Unit21 AI agents** (Apr 2026) | Investigation agents (triage, narratives, close/escalate) and detection agents (rule tuning); optional auto-closure with audit trail, human sampling and proven accuracy | Same safeguards we implement: audit trail, QA sampling, per-queue enablement |
| **Sigma360 AI Investigator Agent** (Jul 2025) | Automates manual review of screening alerts | AML-screening focus |
| **RBIH MuleHunter.AI** (Dec 2024) | ML model that flags likely mule accounts using 19 behaviour patterns; adopted by 23 banks per an RTI reply (Dec 2025) | India-specific mule intelligence; our watchlist and mule rule mirror this input |
| Classic FRM engines (FICO Falcon, SAS Fraud Management, Clari5, etc.) | Real-time scoring and rules | Detect, but leave the investigation manual: the gap we target |

**Technology research:**
- **Orchestration:** n8n (visual, webhook-driven, easy HITL hand-offs) versus code frameworks such as LangGraph or CrewAI. We chose n8n for transparency to non-developers and auditors.
- **Models:** open-weight Llama 3.x, gpt-oss, Qwen, Mistral and Gemma, served free via Google AI Studio (Gemma) and Cerebras (gpt-oss, Qwen) or locally via Ollama, behind one client interface, so the comparison is fair.
- **Reliability:** JSON mode, Pydantic schema validation and repair retries.
- **Safety:** prompt-injection screening (a dedicated classifier such as Llama Prompt Guard 2 is a candidate upgrade), PII pseudonymisation, deterministic routing.
- **UI:** first built in Streamlit (fast to write in Python); replaced by a light single-page web app served by the FastAPI service because Streamlit re-runs the whole script on every click, which made the analyst screen lag. The Streamlit screen is kept as a fallback.

## 1.7 Opportunity justification
* **High volume, high repetition, evidence-heavy:** digital-payment frauds are more than half of all cases, and each alert needs the same 8–10 look-ups. That is ideal for agents.
* **Time-critical:** money moves in minutes. Assembling evidence in seconds and routing by risk (with an SLA monitor) shrinks the response window.
* **Judgement and accountability:** adverse actions (holds, blocks, STRs) affect customers and carry regulatory weight, so humans must stay in the loop. The RBI FREE-AI framework (Aug 2025) stresses accountability, people-first design and explainability.
* **Proven direction:** major vendors are shipping agentic investigation, but as closed products. An open-model, governance-first PoC shows banks how to do it transparently.

**Selected opportunity:** an *Agentic Fraud-Alert Investigation Copilot*. Four specialised agents and a deterministic
routing policy auto-close only clear false positives, route ambiguous cases to L1 analysts and escalate high-risk
cases to L2, with full auditability.

### Sources
- RBI Annual Report 2024-25 fraud figures: [Business Standard, 29 May 2025](https://www.business-standard.com/amp/finance/news/bank-fraud-amount-triples-in-fy25-despite-drop-in-number-of-cases-rbi-125052900696_1.html)
- [NICE Actimize press release, 2 Apr 2025](https://www.niceactimize.com/press-releases/nice-actimize-x-sight-actone-platform-redefines-financial-crime-investigations-with-agentic-ai-476)
- [Feedzai Farol announcement](https://www.thailand-business-news.com/?p=330994)
- [Unit21: AI agents for financial crime](https://www.unit21.ai/blog/how-ai-agents-for-financial-crime-run-the-full-compliance-lifecycle)
- [Sigma360 AI Investigator Agent](https://www.helpnetsecurity.com/2025/07/10/sigma360-ai-investigator-agent/)
- [MediaNama: RTI on MuleHunter.AI adoption](https://www.medianama.com/2025/12/223-rti-23-banks-mulehunter-mule-accounts/)
- [RBI FREE-AI framework summary](https://scrut.io/post/rbi-framework-for-responsible-and-ethical-enablement-of-artificial-intelligence)
- [Gemma on the Gemini API](https://ai.google.dev/gemma/docs/core/gemma_on_gemini_api) · [Cerebras models](https://inference-docs.cerebras.ai/models/overview) · [Free LLM API limits (OpenRouter, Sep 2026)](https://openrouter.ai/blog/tutorials/free-llm-apis-compared/)
