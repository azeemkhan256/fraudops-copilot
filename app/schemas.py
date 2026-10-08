"""Structured-output contracts for every agent. Model output that does not validate is rejected."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

Severity = Literal["LOW", "MEDIUM", "HIGH"]
Band = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
Decision = Literal["PROCEED", "VERIFY", "HOLD", "ESCALATE"]
Typology = Literal["NONE", "ACCOUNT_TAKEOVER", "CARD_TESTING", "MULE_ACCOUNT", "AUTHORISED_PUSH_PAYMENT_SCAM",
                   "IDENTITY_FRAUD", "FIRST_PARTY_FRAUD", "OTHER", "UNCLEAR"]


def _upper(v):
    return v.strip().upper().replace(" ", "_") if isinstance(v, str) else v


def _ids(v):
    if v is None:
        return []
    if isinstance(v, str):
        v = [p.strip() for p in v.replace(";", ",").split(",") if p.strip()]
    return [str(x).strip().upper() for x in v]


class Finding(BaseModel):
    factor: str = Field(min_length=3)
    severity: Severity = "MEDIUM"
    evidence_ids: list[str] = Field(default_factory=list)

    _u = field_validator("severity", mode="before")(_upper)
    _e = field_validator("evidence_ids", mode="before")(_ids)


class Mitigant(BaseModel):
    factor: str = Field(min_length=3)
    evidence_ids: list[str] = Field(default_factory=list)

    _e = field_validator("evidence_ids", mode="before")(_ids)


class TransactionAnalysis(BaseModel):
    anomaly_score: int = Field(ge=0, le=100)
    anomalies: list[Finding] = Field(default_factory=list)
    mitigating_factors: list[Mitigant] = Field(default_factory=list)
    summary: str = Field(min_length=10, max_length=1200)


class CustomerBehaviour(BaseModel):
    behaviour_risk: int = Field(ge=0, le=100)
    consistent_with_profile: bool
    account_takeover_indicators: list[str] = Field(default_factory=list)
    social_engineering_indicators: list[str] = Field(default_factory=list)
    mule_indicators: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=10, max_length=1200)

    _e = field_validator("evidence_ids", mode="before")(_ids)


class RiskPolicy(BaseModel):
    risk_score: int = Field(ge=0, le=100)
    risk_band: Band
    fraud_typology: Typology = "UNCLEAR"
    policy_flags: list[str] = Field(default_factory=list)
    regulatory_notes: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=10, max_length=1200)

    _b = field_validator("risk_band", "fraud_typology", mode="before")(_upper)
    _e = field_validator("evidence_ids", "policy_flags", mode="before")(_ids)


class Recommendation(BaseModel):
    decision: Decision
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=20, max_length=1500)
    evidence_ids: list[str] = Field(min_length=1)
    recommended_actions: list[str] = Field(min_length=1)
    customer_message: str | None = None
    needs_human_review: bool = True

    _d = field_validator("decision", mode="before")(_upper)
    _e = field_validator("evidence_ids", mode="before")(_ids)

    @field_validator("confidence", mode="before")
    @classmethod
    def _pct(cls, v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return v
        return v / 100 if 1 < v <= 100 else v


AGENT_SCHEMAS = {
    "transaction_analysis": TransactionAnalysis,
    "customer_behaviour": CustomerBehaviour,
    "risk_policy": RiskPolicy,
    "recommendation": Recommendation,
}

AGENT_ORDER = ["transaction_analysis", "customer_behaviour", "risk_policy", "recommendation"]

AGENT_TITLES = {
    "transaction_analysis": "Transaction Analysis Agent",
    "customer_behaviour": "Customer Behaviour Agent",
    "risk_policy": "Risk & Policy Agent",
    "recommendation": "Recommendation Agent",
}
