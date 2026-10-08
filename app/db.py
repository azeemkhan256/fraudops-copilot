"""SQLite storage for the synthetic bank, the case-management store and the audit trail."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    customer_id TEXT PRIMARY KEY,
    full_name TEXT, phone TEXT, email TEXT, pan TEXT,          -- synthetic PII, never sent to LLMs
    age INTEGER, segment TEXT, occupation TEXT, home_city TEXT,
    customer_since TEXT, kyc_level TEXT, risk_rating TEXT,
    avg_monthly_spend REAL, prior_fraud_reports INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS accounts (
    account_id TEXT PRIMARY KEY, customer_id TEXT, account_type TEXT,
    account_number TEXT, balance REAL, opened_on TEXT
);
CREATE TABLE IF NOT EXISTS devices (
    device_id TEXT PRIMARY KEY, customer_id TEXT, model TEXT, os TEXT,
    first_seen TEXT, last_seen TEXT, trusted INTEGER, emulator_or_rooted INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS beneficiaries (
    beneficiary_id TEXT PRIMARY KEY, customer_id TEXT, display_name TEXT,
    bank TEXT, account_number TEXT, added_on TEXT, category TEXT
);
CREATE TABLE IF NOT EXISTS transactions (
    txn_id TEXT PRIMARY KEY, account_id TEXT, ts TEXT, direction TEXT,
    amount REAL, channel TEXT, counterparty TEXT, beneficiary_id TEXT,
    mcc TEXT, city TEXT, country TEXT, device_id TEXT, ip_country TEXT,
    remarks TEXT, status TEXT
);
CREATE TABLE IF NOT EXISTS security_events (
    event_id TEXT PRIMARY KEY, customer_id TEXT, ts TEXT, event_type TEXT, details TEXT
);
CREATE TABLE IF NOT EXISTS watchlist (
    entry_id TEXT PRIMARY KEY, entity_type TEXT, entity_value TEXT, source TEXT, reason TEXT
);
CREATE TABLE IF NOT EXISTS prior_alerts (
    id TEXT PRIMARY KEY, customer_id TEXT, ts TEXT, rule_id TEXT, disposition TEXT
);
CREATE TABLE IF NOT EXISTS fraud_rules (
    rule_id TEXT PRIMARY KEY, name TEXT, description TEXT, severity TEXT, weight INTEGER
);
CREATE TABLE IF NOT EXISTS alerts (
    alert_id TEXT PRIMARY KEY, ts TEXT, customer_id TEXT, account_id TEXT, txn_id TEXT,
    source_system TEXT, frm_score INTEGER, trigger_rules TEXT, title TEXT, status TEXT
);
CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY, alert_id TEXT, model_label TEXT, orchestrator TEXT,
    status TEXT, route TEXT, risk_score INTEGER, risk_band TEXT, decision TEXT, confidence REAL,
    evidence_json TEXT, agents_json TEXT, guardrails_json TEXT,
    human_decision TEXT, human_reason TEXT, decided_by TEXT, decided_role TEXT, decided_at TEXT,
    actions_json TEXT, qa_sampled INTEGER DEFAULT 0, error TEXT,
    created_at TEXT, updated_at TEXT, total_latency_ms INTEGER
);
CREATE TABLE IF NOT EXISTS audit_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, case_id TEXT, actor TEXT, actor_role TEXT,
    action TEXT, details TEXT, prev_hash TEXT, hash TEXT
);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, case_id TEXT, channel TEXT, recipient TEXT, message TEXT
);
"""


def connect() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def session() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def row(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> dict[str, Any] | None:
    r = conn.execute(sql, params).fetchone()
    return dict(r) if r else None


def loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default
