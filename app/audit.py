"""Tamper-evident audit trail: every agent call, routing decision and human action is hash-chained."""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime

from . import db


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


_LOCK = threading.RLock()


def log(conn, case_id: str | None, actor: str, action: str, details: dict | str, role: str = "SYSTEM") -> None:
    with _LOCK:  # read-last-hash + insert + commit must be atomic or the chain breaks
        _append(conn, case_id, actor, action, details, role)
        conn.commit()


def _append(conn, case_id, actor, action, details, role) -> None:
    prev = conn.execute("SELECT hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
    prev_hash = prev[0] if prev else "GENESIS"
    ts = now()
    det = details if isinstance(details, str) else json.dumps(details, ensure_ascii=False, default=str)
    digest = hashlib.sha256(f"{prev_hash}|{ts}|{case_id}|{actor}|{role}|{action}|{det}".encode()).hexdigest()
    conn.execute("INSERT INTO audit_log (ts, case_id, actor, actor_role, action, details, prev_hash, hash) "
                 "VALUES (?,?,?,?,?,?,?,?)", (ts, case_id, actor, role, action, det, prev_hash, digest))


def verify_chain(conn) -> dict:
    """Recomputes every hash; any edited or deleted row breaks the chain."""
    prev_hash = "GENESIS"
    entries = db.rows(conn, "SELECT * FROM audit_log ORDER BY seq")
    for e in entries:
        expected = hashlib.sha256(
            f"{prev_hash}|{e['ts']}|{e['case_id']}|{e['actor']}|{e['actor_role']}|{e['action']}|{e['details']}".encode()
        ).hexdigest()
        if e["prev_hash"] != prev_hash or e["hash"] != expected:
            return {"valid": False, "entries": len(entries), "broken_at_seq": e["seq"]}
        prev_hash = e["hash"]
    return {"valid": True, "entries": len(entries), "broken_at_seq": None}
