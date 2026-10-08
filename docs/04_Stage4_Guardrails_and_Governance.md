# Stage 4: AI Guardrails & Governance / Compliance

## 4.1 Framework selection
| Framework | Why used | Role in this project |
|---|---|---|
| **NIST AI Risk Management Framework 1.0** (GOVERN, MAP, MEASURE, MANAGE) | Risk-based, technology-neutral, widely used for AI risk mapping | **Primary framework** for the mapping table |
| **ISO/IEC 42001:2023** (AI management system, Annex A controls) | Certifiable management-system standard a bank would adopt | Cross-reference for controls (A.2 policy … A.10 third parties) |
| **RBI FREE-AI framework** (Aug 2025: 7 Sutras, 6 pillars, 26 recommendations) | Sector-specific expectations for Indian regulated entities | Sector alignment: board-approved AI policy, accountability, people-first, understandable by design |
| ISO/IEC 27001:2022 · DPDP Act 2023 | Information security and personal-data protection | Access control, logging, data masking, data minimisation |

*Check exact clause numbers against the official texts before final submission. The mapping below uses function / control-area level.*

## 4.2 Guardrails draft (by layer)
* **Data guardrails:**
  * Synthetic data only.
  * Customer and individual-payee names, phone, e-mail, PAN and account numbers are pseudonymised before any model call.
  * Every prompt passes a PII-leak check.
  * Keys stay in `.env` (never committed); n8n holds no credentials.
* **Model guardrails:**
  * Untrusted free text is screened for prompt injection and quarantined.
  * STOIC prompts declare remarks as data.
  * Outputs must match Pydantic schemas (one repair retry, then the exception route).
  * Grounding check: cited evidence IDs must exist, and risk claims (SIM swap, watchlist, emulator…) must be supported by data.
  * The rules score is a floor the model cannot lower.
  * Temperature 0; prompt version recorded.
* **Action guardrails:**
  * Agents cannot act. A deterministic routing policy decides A/H/E/X.
  * The only autonomous outcome is closing a low-risk false positive (reversible), and 10% of those are QA-sampled.
  * Actions come from an allow-list. Blocking a card and freezing debits are L2-only.
  * Overrides and exception decisions need a written reason.
  * A 15-minute SLA escalates stale cases.
* **Human oversight:**
  * L1 decides H-route cases; L2 decides escalations; Auditor has read-only access.
  * The full agent trace and evidence are shown before every decision.
* **Auditability:**
  * SHA-256 hash-chained audit log of every agent call (model, prompt version, latency, validity, grounding), route, notification, human decision and action.
  * Tamper check is available in the UI and via `GET /audit/verify`.

## 4.3 Mapping: Risk → Guardrail → Governance requirement → Implementation in the PoC
| # | Risk | Guardrail | Governance requirement | Implementation in PoC (evidence) |
|---|---|---|---|---|
| 1 | **Privacy**: customer PII sent to an external model provider | Pseudonymisation + PII-leak check + synthetic data | NIST MEASURE (privacy risk examined) · ISO 42001 A.7 (data for AI systems) · DPDP Act purpose limitation & data minimisation · FREE-AI *Protection* pillar | `tools.build_evidence_pack` → `guardrails.redact_pii`; `agents.run_agent` → `pii_leak_check`. Test `test_pii_redaction_and_no_pii_in_prompts`. Evidence-pack tab shows `[NAME]` and `XXXX1234` |
| 2 | **Confidential data / secrets**: API keys or credentials exposed | Keys only in local `.env`, git-ignored; LLM calls only from the backend; n8n stores no secrets | ISO 27001 A.5/A.8 (access & secret management) · ISO 42001 A.6 (life-cycle controls) | `.env.example`, `.gitignore`; `app/llm.py` reads keys from environment only |
| 3 | **Hallucination**: model invents events or cites non-existent evidence | Numbered facts with mandatory citations; grounding check; flagged cases cannot auto-close | NIST MEASURE (valid & reliable; explainable) · ISO 42001 A.6.2 (verification & validation) · FREE-AI *Understandable by design* | `guardrails.grounding_check`, flag `grounding_issue` in `decide_route`. Test `test_grounding_detects_unsupported_claims_and_bad_citations`. Benchmark metric: hallucination rate |
| 4 | **Malformed output** breaks automation | Pydantic schemas, tolerant JSON extraction, one repair retry, then exception route X | NIST MEASURE (reliability) · MANAGE (respond to failures) | `app/schemas.py`, `llm.parse_json`, `agents.run_agent`. Tests `test_llm_client_handles…`, `test_llm_failure_routes_to_exception`. Metric: valid JSON first try / after repair |
| 5 | **Prompt injection** in payment remarks or merchant names | Injection screen removes instruction-like text; remarks labelled UNTRUSTED; injection attempt raised as a fraud signal; rules floor stops a "risk 0" downgrade | NIST MEASURE (security & resilience) · ISO 27001 secure development · FREE-AI *Safety, resilience* | `guardrails.screen_untrusted_text`; case ALT-3004; test `test_prompt_injection_is_quarantined`; benchmark *injection stress test* (screen off) shows what the guardrail prevents |
| 6 | **Unsafe autonomous action**: AI blocks or releases money wrongly | Agents only recommend; deterministic routing; action allow-list; auto-close only for PROCEED with risk < 30, confidence ≥ 0.8, no flags, no HIGH rule; API refuses routes that conflict with the policy (HTTP 409) | NIST MAP (human oversight defined) · MANAGE (mechanisms to override / deactivate) · ISO 42001 A.9 (responsible use) · FREE-AI *People first*, *Accountability* | `guardrails.decide_route`, `guardrails.ACTIONS`, `orchestrator.apply_route`. Test `test_api_step_sequence` (409 on bypass) |
| 7 | **Access control**: wrong person approves high-impact actions | RBAC: L1, L2, Auditor; E-route cases and blocking actions L2-only; Auditor read-only | NIST GOVERN (roles & responsibilities) · ISO 27001 access control · ISO 42001 A.3 (roles) | `orchestrator.human_decision`, `guardrails.allowed_decisions`. Test `test_rbac_and_override_rules`; n8n WF-02 returns 403 |
| 8 | **Decision thresholds** set ad hoc or changed silently | Thresholds held as configuration (`.env`), shown in UI, recorded in every routing decision; changes go through change control | NIST GOVERN (policies & procedures) · FREE-AI *Policy* pillar (board-approved AI policy) | `config.AUTO_CLOSE_MAX_RISK`, `AUTO_CLOSE_MIN_CONFIDENCE`, `ESCALATE_MIN_RISK`; Governance page |
| 9 | **Automation bias**: humans rubber-stamp AI | Evidence and agent trace shown; mandatory reason to override; analysts can refer to L2; QA sampling | NIST MAP/MEASURE (human-AI configuration) · FREE-AI *Accountability* | Case queues page (decision panel); `human_decision` 422 without reason; `qa_sampled` flag |
| 10 | **Bias / unfair treatment** of customer groups | No names, gender, religion or location-of-origin in prompts; only an age *band* used, and only protectively (scam-vulnerability policy P3); monitor false-positive rate by segment | NIST MEASURE (fairness & bias evaluated) · FREE-AI *Fairness & equity* | `tools.build_evidence_pack` (age band, pseudonyms). **Limitation:** 13 cases are too few for statistical fairness testing; recommend segment-level FPR monitoring in a pilot |
| 11 | **Lack of auditability** for regulators and internal audit | Hash-chained log of every step incl. model, prompt version, inputs summary, outputs, decisions, actions; tamper verification | NIST MEASURE (transparency & accountability) · ISO 42001 A.6.2.8 (event logs) · ISO 27001 logging · FREE-AI *Assurance* | `app/audit.py`, `GET /audit/verify`. Test `test_audit_chain_detects_tampering`; Governance page |
| 12 | **Model risk, drift, vendor lock-in** | Benchmark 3+ open models on fixed cases before adoption; provider-agnostic gateway; prompt versioning; Python fallback | NIST MANAGE (post-deployment monitoring) · ISO 42001 A.10 (third-party relationships) · FREE-AI *Governance* (model life-cycle) | `benchmark/run_benchmark.py`, `models.json`, `prompts.PROMPT_VERSION` |
| 13 | **Availability / time-critical failures**: data missing, model down, case stuck | Exception route X with ops notification; n8n error outputs; 15-min SLA monitor | NIST MANAGE (incident response) · FREE-AI *Safety, resilience* | ALT-2003 exception case; WF-01 "Technical Exception"; WF-03 SLA monitor; `POST /cases/sla/check` |
| 14 | **Poor explainability** to customer, compliance or regulator | Cited rationale, typology, policy flags; STR draft generated only after L2 confirmation | NIST MEASURE (explainable & interpretable) · FREE-AI *Understandable by design* | Recommendation output; `human_decision` → `str_draft` |

## 4.4 Governance operating model (proposed)
| Role | Responsibility |
|---|---|
| Board / Risk Committee | Approves the AI policy and risk appetite, including auto-close thresholds (FREE-AI Policy pillar) |
| Head of Fraud Operations (AI system owner) | Accountable for outcomes, SLA, QA-sampling results; receives SLA breaches |
| Model Risk Management | Validates models before use (benchmark), re-validates on model or prompt change |
| Compliance / AML | Reviews STR drafts and files with FIU-IND; monitors regulatory changes |
| Information Security | Key management, access reviews, injection-pattern updates |
| Internal Audit | Read-only access; verifies the audit chain and samples decisions |

**AI incident handling:** a wrong auto-closure found in QA, or a guardrail bypass, is logged as an incident. The response is to
freeze auto-close (set `AUTO_CLOSE_MAX_RISK=0`, so everything goes to humans), find the root cause (prompt, model, rule), fix it,
re-run the benchmark and record the change.

## Sources
- [NIST AI RMF 1.0](https://www.nist.gov/itl/ai-risk-management-framework)
- [ISO/IEC 42001:2023](https://www.iso.org/standard/42001)
- [RBI FREE-AI framework (Aug 2025) summary](https://scrut.io/post/rbi-framework-for-responsible-and-ethical-enablement-of-artificial-intelligence) · [Outlook Money: the seven Sutras](https://www.outlookmoney.com/banking/free-ai-rbi-releases-framework-for-ai-use-in-financial-sector-learn-about-its-seven-sutras)
