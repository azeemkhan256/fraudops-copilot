# Stage 2: Process Mapping & Agentic AI Architecture

Diagrams (PNG in `docs/diagrams/`, editable sources in `docs/diagrams/src/`):

| # | Diagram | File |
|---|---|---|
| 1 | AS-IS process | `01_as_is_process.png` (Mermaid source `src/01_as_is_process.mmd`) |
| 2 | TO-BE agentic process with A/H/E marking | `02_to_be_agentic_process.png` (`src/02_to_be_agentic_process.mmd`) |
| 3 | Technical & governance architecture | `03_technical_governance_architecture.png` (`src/03_architecture.html`) |
| – | Systems-thinking causal loops (Stage 1) | `00_systems_thinking_loops.png` |

To edit a Mermaid diagram, paste its `.mmd` file into <https://mermaid.live> and export a PNG.

## 2.1 AS-IS process (current manual / semi-manual)
**Actors:** customer, FRM rules engine, L1 fraud analyst, siloed source systems, contact centre, L2 senior investigator, compliance/AML.

| Step | Activity | System(s) | Delay / bottleneck / exception |
|---|---|---|---|
| 1 | Customer (or fraudster) makes a UPI / IMPS / card transaction | Channel apps, switch | Push payments settle instantly |
| 2 | FRM engine applies static rules; alert created if a threshold is breached | FRM | Most alerts are false positives |
| 3 | Alert waits in a FIFO queue | Case tool / e-mail | ⏱ Queue wait at peak (nights, weekends, salary days) |
| 4 | L1 analyst manually checks 6–8 screens: CBS history, card/UPI switch, device & login logs, CRM/KYC, beneficiary module, negative lists | Siloed systems | 🔴 **Main bottleneck**: swivel-chair look-ups, 15–25 min per alert (indicative, to be validated with the expert) |
| 5 | Analyst writes free-text notes and decides on judgement | — | No standard reasoning; inconsistent between analysts |
| 6a | Genuine → close alert | Case tool | Reason often not captured, so no learning loop |
| 6b | Unsure → contact-centre call-back | Contact centre | ⏱ Hours; ⚠️ customer unreachable, so the alert is parked |
| 6c | Suspicious → escalate by e-mail / phone | E-mail | ⏱ Hand-off delay |
| 7 | L2 re-collects the same evidence, decides block/freeze | Same systems | 🔴 Duplicate effort |
| 8 | Compliance drafts STR manually | AML system | ⏱ Days |
| Exception | Source system down or data missing | — | ⚠️ Alert parked with no SLA or owner |
| Exception | Money already moved before review; customer reports via 1930 | — | Loss already occurred |

## 2.2 TO-BE agentic process
Marking: **A = Autonomous**, **H = Human-in-the-Loop**, **E = Escalation** (plus **X = exception route**, which ends in human handling).

| # | Activity | Who / what | Mark |
|---|---|---|---|
| 1 | Alert received by n8n webhook; case opened with model and prompt version | n8n WF-01 | **A** |
| 2 | Evidence tool gathers 8 sources, computes features, re-runs 15 fraud rules, pseudonymises PII, screens remarks for prompt injection | Tools API | **A** |
| 3 | Evidence incomplete → exception queue, ops notified, analyst handles manually | n8n IF → X | **E** → **H** |
| 4 | Transaction Analysis Agent: anomalies vs baseline, mitigating factors | LLM agent | **A** |
| 5 | Customer Behaviour Agent: takeover / scam / mule indicators | LLM agent | **A** |
| 6 | Risk & Policy Agent: risk score, band, typology, policies | LLM agent | **A** |
| 7 | Recommendation Agent: PROCEED / VERIFY / HOLD / ESCALATE, cited rationale | LLM agent | **A** (recommendation only) |
| 8 | Schema validation (1 repair retry); invalid → exception | Guardrail | **A** / X |
| 9 | Routing policy: rules-score floor, grounding, confidence, injection flag, thresholds | Guardrail (deterministic) | **A** |
| 10 | Route A: auto-close clear false positive (risk < 30, confidence ≥ 0.8, no flags) | System | **A** |
| 11 | 10% of auto-closures sampled for QA review | L1 analyst | **H** |
| 12 | Route H: analyst reviews agent trace + evidence; approves or overrides (reason mandatory) | L1 analyst | **H** |
| 13 | Permitted action executed (release, step-up verification, 24 h hold) | n8n WF-02 | **A** (after H approval) |
| 14 | Route E: L2 notified, 15-minute SLA starts | n8n | **E** |
| 15 | L2 decides; only L2 may block a card or freeze debits | Senior investigator | **E** |
| 16 | Protective actions executed; STR draft sent to compliance; customer advised on 1930 | n8n WF-02 | **A** (after E approval) |
| 17 | Compliance reviews and files the STR | Compliance | **H** |
| 18 | SLA monitor: undecided cases older than 15 min go to the Head of Fraud Ops | n8n WF-03 | **E** |
| 19 | Every step written to a hash-chained audit trail | System | **A** |

**Agent roles** (each has one job, one output schema and no ability to act):

| Agent | Input | Output | Why separate |
|---|---|---|---|
| Transaction Analysis | Evidence facts | anomaly score, anomalies, mitigating factors | Isolates *what is unusual* from *who did it* |
| Customer Behaviour | Facts + Agent 1 | behaviour risk, takeover / scam / mule indicators | Different typologies need different evidence (device, security events, inbound credits) |
| Risk & Policy | Facts + Agents 1–2 + rules + policy P1–P7 | risk score, band, typology, policy flags | Policy grounding, calibrated to one risk scale |
| Recommendation | Facts + Agents 1–3 | decision, confidence, cited rationale, actions, customer message | Produces something a human can approve in seconds |

**Escalation mapping:**
- *Automatic E* when the model recommends ESCALATE, the final risk is ≥ 70, or any CRITICAL rule fires (mule watchlist R05, mule pattern R11).
- *Analyst-initiated E*: an L1 analyst chooses ESCALATE, which refers the case to L2 with no blocking actions.
- *SLA E*: the Head of Fraud Ops is alerted when a decision is still pending after 15 minutes.

## 2.3 Technical & governance architecture
Seven layers (see diagram 3):
1. UI / UX (light web app served by the FastAPI service; classic Streamlit screen kept as a fallback)
2. Orchestration (n8n workflows WF-01/02/03, plus the identical built-in engine, which runs agents 1 and 2 in parallel)
3. Agents (FastAPI, STOIC prompts, schemas)
4. Models (open-weight Gemma via Google AI Studio; gpt-oss and Qwen via Cerebras; Ollama locally)
5. Tools & data (evidence builder, rules engine, watchlists, synthetic SQLite)
6. Guardrails (data / model / action)
7. Human oversight & governance (L1/L2/auditor, benchmark, framework mapping)

**Integration contract** (WF-01, each arrow is an HTTP call from n8n):
`/cases/start → /tools/evidence → /agents/transaction_analysis → /agents/customer_behaviour → /agents/risk_policy → /agents/recommendation → /guardrails/route → /cases/{id}/apply/{A|H|E|X}`

Any failed call leaves the node's error output and is returned to the UI as a technical exception.

**Design decisions:**
- *Agents never call actions.* Only the routing policy and humans can trigger actions, and the API refuses any route that conflicts with the policy (HTTP 409).
- *Deterministic routing outside the LLM.* Thresholds are configuration, not prompt text, so they can be audited and changed under change control.
- *Rules score is a floor.* A model can raise risk but never lower it below what the bank's rules computed. This prevents an LLM (or an injected instruction) from talking a case down.
- *Same pipeline for demo and benchmark.* n8n and the Python engine call identical step functions, so benchmark results represent the live system.
