"""Deterministic investigation tools the agents rely on.

These are the 'tools/data integration' layer: they query the (synthetic) core-banking, device,
security-event, beneficiary and watchlist sources, compute behavioural features, run the bank's
fraud-rule catalogue and assemble a citation-ready evidence pack. No LLM is involved here.
"""
from __future__ import annotations

import statistics
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from . import config, db, guardrails

FMT = "%Y-%m-%d %H:%M:%S"


class DataUnavailable(Exception):
    """A required source system returned nothing - the case must go to the exception queue."""


def _dt(s: str) -> datetime:
    return datetime.strptime(s, FMT)


def mask_account(number: str | None) -> str:
    if not number:
        return "n/a"
    return "XXXX" + number[-4:]


def _age_band(age: int) -> str:
    lo = (age // 10) * 10
    return f"{lo}-{lo + 9}"


def _inr(x: float) -> str:
    return f"Rs {x:,.0f}"


# --------------------------------------------------------------------------- source-system lookups
def get_alert(conn, alert_id: str) -> dict:
    a = db.row(conn, "SELECT * FROM alerts WHERE alert_id=?", (alert_id,))
    if not a:
        raise DataUnavailable(f"Alert {alert_id} not found in alert store")
    a["trigger_rules"] = db.loads(a["trigger_rules"], [])
    return a


def get_customer(conn, customer_id: str) -> dict:
    c = db.row(conn, "SELECT * FROM customers WHERE customer_id=?", (customer_id,))
    if not c:
        raise DataUnavailable(f"Customer master record for {customer_id} unavailable (CRM/CBS lookup returned no data)")
    return c


def compute_features(conn, alert: dict) -> dict[str, Any]:
    """All behavioural signals for the triggering transaction, computed from source data."""
    txn = db.row(conn, "SELECT * FROM transactions WHERE txn_id=?", (alert["txn_id"],))
    if not txn:
        raise DataUnavailable(f"Transaction {alert['txn_id']} not found in core banking")
    cust = get_customer(conn, alert["customer_id"])
    acct = db.row(conn, "SELECT * FROM accounts WHERE account_id=?", (alert["account_id"],))
    if not acct:
        raise DataUnavailable(f"Account {alert['account_id']} not found in core banking")

    t_time = _dt(txn["ts"])
    hist = db.rows(conn, "SELECT * FROM transactions WHERE account_id=? AND ts<? ORDER BY ts", (acct["account_id"], txn["ts"]))
    hist_90 = [h for h in hist if _dt(h["ts"]) >= t_time - timedelta(days=90)]
    debits_90 = [h for h in hist_90 if h["direction"] == "DR"]
    amounts = [h["amount"] for h in debits_90] or [0.0]
    avg_debit = statistics.mean(amounts)
    hours = Counter(_dt(h["ts"]).hour for h in debits_90)
    night_share = sum(v for k, v in hours.items() if k < 5) / max(1, len(debits_90))
    channels = Counter(h["channel"] for h in debits_90)
    cities = Counter(h["city"] for h in debits_90 if h["city"] and h["city"] != "Online")
    countries = Counter(h["country"] for h in hist_90)

    # beneficiary
    ben = None
    if txn["beneficiary_id"]:
        ben = db.row(conn, "SELECT * FROM beneficiaries WHERE beneficiary_id=?", (txn["beneficiary_id"],))
    ben_age_h = None
    ben_prior_payments = 0
    ben_watchlisted = False
    if ben:
        ben_age_h = (t_time - _dt(ben["added_on"])).total_seconds() / 3600 if len(ben["added_on"]) > 10 else \
            (t_time - datetime.strptime(ben["added_on"], "%Y-%m-%d")).total_seconds() / 3600
        ben_prior_payments = sum(1 for h in hist if h["beneficiary_id"] == ben["beneficiary_id"])
        ben_watchlisted = bool(db.row(conn, "SELECT 1 AS x FROM watchlist WHERE entity_type='beneficiary_account' AND entity_value=?",
                                      (ben["account_number"],)))

    # device
    dev = db.row(conn, "SELECT * FROM devices WHERE device_id=?", (txn["device_id"],)) if txn["device_id"] else None
    dev_age_h = (t_time - _dt(dev["first_seen"])).total_seconds() / 3600 if dev and len(dev["first_seen"]) > 10 else \
        ((t_time - datetime.strptime(dev["first_seen"], "%Y-%m-%d")).total_seconds() / 3600 if dev else None)
    dev_watchlisted = bool(dev and db.row(conn, "SELECT 1 AS x FROM watchlist WHERE entity_type='device' AND entity_value=?",
                                          (dev["device_id"],)))

    # security events, last 72h
    sec = [e for e in db.rows(conn, "SELECT * FROM security_events WHERE customer_id=? ORDER BY ts", (cust["customer_id"],))
           if timedelta(0) <= t_time - _dt(e["ts"]) <= timedelta(hours=72)]
    sim_swap = any(e["event_type"] in ("SIM_SWAP", "MOBILE_CHANGE") for e in sec)
    pwd_reset_24h = any(e["event_type"] == "PASSWORD_RESET" and t_time - _dt(e["ts"]) <= timedelta(hours=24) for e in sec)
    otp_fail_1h = sum(1 for e in sec if e["event_type"] == "OTP_FAILED" and t_time - _dt(e["ts"]) <= timedelta(hours=1))

    # velocity
    recent_debits_10m = [h for h in hist if h["direction"] == "DR" and t_time - _dt(h["ts"]) <= timedelta(minutes=10)]
    velocity_10m = len(recent_debits_10m) + 1
    small_cnp_15m = [h for h in hist if h["channel"] == "CARD_ECOM" and h["amount"] < 100
                     and t_time - _dt(h["ts"]) <= timedelta(minutes=15)]
    small_cnp_merchants = len({h["counterparty"] for h in small_cnp_15m})

    # balance
    balance_before = acct["balance"]
    drain_pct = 100.0 * txn["amount"] / balance_before if balance_before else 0.0

    # inbound credits / mule
    credits_48h = [h for h in hist if h["direction"] == "CR" and t_time - _dt(h["ts"]) <= timedelta(hours=48)
                   and "payroll" not in (h["counterparty"] or "").lower()]
    outbound_48h = sum(h["amount"] for h in hist if h["direction"] == "DR" and t_time - _dt(h["ts"]) <= timedelta(hours=48))
    credits_total = sum(h["amount"] for h in credits_48h)
    distinct_senders = len({h["counterparty"] for h in credits_48h})

    # geography / impossible travel
    last_physical = next((h for h in reversed(hist) if h["channel"] in ("CARD_POS", "ATM")), None)
    impossible_travel = False
    travel_note = "No physical-presence transaction to compare"
    if last_physical:
        gap_min = (t_time - _dt(last_physical["ts"])).total_seconds() / 60
        txn_loc_country = txn["ip_country"] if txn["channel"] in ("IMPS", "UPI", "NETBANKING", "NEFT", "RTGS") else txn["country"]
        if txn_loc_country != last_physical["country"] and gap_min < 240:
            impossible_travel = True
        travel_note = (f"Last card-present use: {last_physical['city']} ({last_physical['country']}) "
                       f"{gap_min:.0f} min before this transaction")
    foreign = txn["country"] not in ("IN", None, "") or txn["ip_country"] not in ("IN", None, "")
    away_from_home = txn["city"] not in (cust["home_city"], "Online", "")
    countries_seen = sorted(c for c in countries if c and c != "IN")

    prior = db.rows(conn, "SELECT ts, rule_id, disposition FROM prior_alerts WHERE customer_id=?", (cust["customer_id"],))
    last5 = [h for h in hist if h["direction"] == "DR"][-5:]
    last_credits = [h for h in hist if h["direction"] == "CR"][-3:]

    return {
        "txn": txn, "customer": cust, "account": acct, "beneficiary": ben, "device": dev,
        "security_events": sec, "prior_alerts": prior, "last_debits": last5, "last_credits": last_credits,
        "amount": txn["amount"], "avg_debit_90d": round(avg_debit, 2), "max_debit_90d": max(amounts),
        "amount_ratio": round(txn["amount"] / avg_debit, 1) if avg_debit else None, "debit_count_90d": len(debits_90),
        "night_share_90d": round(night_share, 2), "top_channels": [c for c, _ in channels.most_common(3)],
        "top_cities": [c for c, _ in cities.most_common(3)], "countries_seen_90d": countries_seen,
        "txn_hour": t_time.hour, "beneficiary_new": ben is not None and ben_age_h is not None and ben_age_h < 24,
        "beneficiary_age_h": None if ben_age_h is None else round(ben_age_h, 1),
        "beneficiary_prior_payments": ben_prior_payments, "beneficiary_watchlisted": ben_watchlisted,
        "device_new": dev is not None and dev_age_h is not None and dev_age_h < 72,
        "device_age_h": None if dev_age_h is None else round(dev_age_h, 1),
        "device_trusted": bool(dev and dev["trusted"]), "device_compromised": bool(dev and dev["emulator_or_rooted"]),
        "device_watchlisted": dev_watchlisted,
        "sim_swap_72h": sim_swap, "password_reset_24h": pwd_reset_24h, "otp_failures_1h": otp_fail_1h,
        "velocity_10m": velocity_10m, "small_cnp_merchants_15m": small_cnp_merchants,
        "balance_before": balance_before, "drain_pct": round(drain_pct, 1),
        "credits_48h": len(credits_48h), "credits_48h_total": credits_total, "distinct_senders_48h": distinct_senders,
        "outbound_48h_total": outbound_48h,
        "impossible_travel": impossible_travel, "travel_note": travel_note, "foreign": foreign,
        "away_from_home": away_from_home, "age": cust["age"],
    }


SCAM_KEYWORDS = ("cbi", "police", "customs", "legal case", "digital arrest", "kyc update", "do not inform",
                 "lottery", "parcel", "arrest warrant", "settlement for")


def run_rules(conn, f: dict) -> list[dict]:
    """Bank fraud-rule catalogue (deterministic). Returns the rules that fire, with evidence."""
    rules = {r["rule_id"]: r for r in db.rows(conn, "SELECT * FROM fraud_rules")}
    txn = f["txn"]
    remarks = (txn["remarks"] or "").lower()
    hits: list[tuple[str, str]] = []
    if txn["direction"] == "DR" and f["amount_ratio"] and f["amount_ratio"] >= 5:
        hits.append(("R01", f"{_inr(f['amount'])} is {f['amount_ratio']}x the 90-day average debit"))
    if f["beneficiary_new"] and f["amount"] >= 25000:
        hits.append(("R02", f"Beneficiary added {f['beneficiary_age_h']}h before a {_inr(f['amount'])} transfer"))
    if f["device_new"]:
        hits.append(("R03", f"Device first seen {f['device_age_h']}h ago"))
    if f["sim_swap_72h"]:
        hits.append(("R04", "SIM swap / mobile change event in last 72h"))
    if f["beneficiary_watchlisted"]:
        hits.append(("R05", "Beneficiary account matches mule-account watchlist"))
    if f["velocity_10m"] >= 5:
        hits.append(("R06", f"{f['velocity_10m']} debits within 10 minutes"))
    if f["drain_pct"] >= 80:
        hits.append(("R07", f"Debit equals {f['drain_pct']}% of available balance"))
    if f["txn_hour"] < 5:
        hits.append(("R08", f"Transaction at {f['txn_hour']:02d}:xx IST"))
    if f["away_from_home"] or f["foreign"] or f["impossible_travel"]:
        hits.append(("R09", "Location differs from home city/country" + (" (impossible travel)" if f["impossible_travel"] else "")))
    if f["device_compromised"]:
        hits.append(("R10", "Device flagged as emulator/rooted"))
    if f["credits_48h"] >= 10 and f["distinct_senders_48h"] >= 8 and \
            f["outbound_48h_total"] + f["amount"] >= 0.7 * f["credits_48h_total"]:
        hits.append(("R11", f"{f['credits_48h']} credits from {f['distinct_senders_48h']} senders in 48h, then rapid outbound"))
    if f["small_cnp_merchants_15m"] >= 4:
        hits.append(("R12", f"{f['small_cnp_merchants_15m']} sub-Rs 100 card-not-present debits at different merchants in 15 min"))
    if f["password_reset_24h"]:
        hits.append(("R13", "Password/MPIN reset in last 24h"))
    if f["age"] >= 60 and any(k in remarks for k in SCAM_KEYWORDS):
        hits.append(("R14", "Remarks match a scam narrative and customer is 60+"))
    if f["otp_failures_1h"] >= 3:
        hits.append(("R15", f"{f['otp_failures_1h']} failed OTP attempts in last hour"))
    return [{"rule_id": rid, "name": rules[rid]["name"], "severity": rules[rid]["severity"],
             "weight": rules[rid]["weight"], "evidence": ev} for rid, ev in hits]


def rule_score(hits: list[dict]) -> int:
    return min(100, sum(h["weight"] for h in hits))


def band(score: int) -> str:
    if score >= 80:
        return "CRITICAL"
    if score >= 60:
        return "HIGH"
    if score >= 30:
        return "MEDIUM"
    return "LOW"


def build_evidence_pack(conn, alert_id: str) -> dict:
    """Gathers every source, runs the rules, and returns a pseudonymised, citation-ready evidence pack.

    Raises DataUnavailable when a mandatory source is missing (handled as an exception route).
    """
    alert = get_alert(conn, alert_id)
    f = compute_features(conn, alert)
    hits = run_rules(conn, f)
    txn, cust = f["txn"], f["customer"]

    # Untrusted free text is screened and quarantined before any model sees it.
    remarks_raw = txn["remarks"] or ""
    screen = guardrails.screen_untrusted_text(remarks_raw)
    if not config.INJECTION_SCREEN_ENABLED:  # stress-test mode: model sees the raw (PII-redacted) remarks
        screen = {"sanitised": guardrails.redact_pii(remarks_raw), "injection_detected": False,
                  "patterns": screen["patterns"], "shadow_detected": screen["injection_detected"]}

    facts: list[tuple[str, str]] = []
    add = lambda src, text: facts.append((src, text))  # noqa: E731
    add("alert", f"Alert {alert['alert_id']} raised by {alert['source_system']} at {alert['ts']} with FRM score "
                 f"{alert['frm_score']}/100; upstream rules: {', '.join(alert['trigger_rules'])}.")
    ch = txn["channel"]
    where = f"{txn['city']} ({txn['country']})" if txn["city"] else txn["country"]
    add("transaction", f"Triggering transaction {txn['txn_id']}: {ch} {txn['direction']} {_inr(txn['amount'])} to "
                       f"'{txn['counterparty']}' at {txn['ts']} IST, location {where}, IP country {txn['ip_country']}, "
                       f"status {txn['status']}.")
    add("customer", f"Customer {cust['customer_id']}: age band {_age_band(cust['age'])}, segment {cust['segment']}, "
                    f"occupation '{cust['occupation']}', home city {cust['home_city']}, customer since "
                    f"{cust['customer_since']}, KYC {cust['kyc_level']}, internal risk rating {cust['risk_rating']}, "
                    f"typical monthly spend {_inr(cust['avg_monthly_spend'])}.")
    add("baseline", f"90-day baseline: {f['debit_count_90d']} debits, average {_inr(f['avg_debit_90d'])}, maximum "
                    f"{_inr(f['max_debit_90d'])}; usual channels {', '.join(f['top_channels']) or 'n/a'}; usual cities "
                    f"{', '.join(f['top_cities']) or 'n/a'}; share of debits between 00:00-05:00 = {f['night_share_90d']:.0%}; "
                    f"foreign countries used: {', '.join(f['countries_seen_90d']) or 'none'}.")
    add("amount", f"This amount is {f['amount_ratio']}x the 90-day average debit.")
    ben = f["beneficiary"]
    if ben:
        add("beneficiary", f"Beneficiary {ben['beneficiary_id']} (category '{ben['category']}', bank {ben['bank']}, "
                           f"account {mask_account(ben['account_number'])}) was added {f['beneficiary_age_h']}h before "
                           f"this transaction; {f['beneficiary_prior_payments']} earlier payments to this beneficiary.")
    else:
        add("beneficiary", "No registered beneficiary involved (merchant or biller payment).")
    add("watchlist", "Beneficiary account IS on the mule-account watchlist." if f["beneficiary_watchlisted"]
        else "Beneficiary/counterparty not found on the mule-account watchlist.")
    dev = f["device"]
    if dev:
        add("device", f"Device {dev['device_id']} ({dev['model']}, {dev['os']}) first seen {f['device_age_h']}h ago; "
                      f"trusted={bool(dev['trusted'])}; emulator/rooted={bool(dev['emulator_or_rooted'])}; "
                      f"on device-reputation watchlist={f['device_watchlisted']}.")
    else:
        add("device", "Card-present / no digital device involved in this transaction.")
    if f["security_events"]:
        ev = "; ".join(f"{e['ts']} {e['event_type']} ({e['details']})" for e in f["security_events"])
        add("security", f"Security events in last 72h: {ev}.")
    else:
        add("security", "No security events (SIM swap, password reset, OTP failure, new-device login) in last 72h.")
    add("velocity", f"{f['velocity_10m']} debit(s) in the 10 minutes up to and including this transaction; "
                    f"{f['small_cnp_merchants_15m']} distinct merchants with sub-Rs 100 card-not-present debits in 15 minutes.")
    add("balance", f"Available balance before transaction {_inr(f['balance_before'])}; this debit is {f['drain_pct']}% of it.")
    add("inbound", f"Inbound credits in last 48h (excl. salary): {f['credits_48h']} credits from "
                   f"{f['distinct_senders_48h']} distinct senders totalling {_inr(f['credits_48h_total'])}; "
                   f"outbound debits in same window {_inr(f['outbound_48h_total'])}.")
    add("geo", f"{f['travel_note']}. Impossible-travel flag={f['impossible_travel']}; outside home city={f['away_from_home']}; "
               f"foreign merchant or IP={f['foreign']}.")
    if f["prior_alerts"]:
        add("history", "Prior alerts: " + "; ".join(f"{p['ts'][:10]} {p['rule_id']} -> {p['disposition']}" for p in f["prior_alerts"]) + ".")
    else:
        add("history", "No prior fraud alerts for this customer.")
    recent = "; ".join(f"{h['ts'][5:16]} {h['channel']} {_inr(h['amount'])} '{h['counterparty']}' ({h['city']})"
                       for h in f["last_debits"])
    add("recent", f"Last debits before this one: {recent or 'none'}.")
    if f["last_credits"]:
        add("recent_credits", "Last credits: " + "; ".join(f"{h['ts'][5:16]} {_inr(h['amount'])} '{h['counterparty']}'"
                                                          for h in f["last_credits"]) + ".")
    if hits:
        add("rules", "Rules engine hits: " + "; ".join(f"{h['rule_id']} {h['name']} [{h['severity']}]: {h['evidence']}" for h in hits) + ".")
    else:
        add("rules", "Rules engine: no rules fired on re-evaluation.")
    add("remarks", "Customer-entered remarks are provided separately as UNTRUSTED DATA"
                   + (" and were flagged by the prompt-injection screen." if screen["injection_detected"] else "."))

    # Pseudonymise: customer and individual-payee names never leave the bank boundary.
    person_names = [cust["full_name"]]
    if ben and ben["category"].lower().startswith("individual - "):
        person_names.append(ben["display_name"].split(" (")[0])
    for b in db.rows(conn, "SELECT display_name, category FROM beneficiaries WHERE customer_id=?", (cust["customer_id"],)):
        if b["category"].lower().startswith("individual - "):
            person_names.append(b["display_name"].split(" (")[0])
    fact_list = [{"id": f"E{i + 1}", "source": s, "text": guardrails.redact_pii(t, person_names)}
                 for i, (s, t) in enumerate(facts)]
    score = rule_score(hits)
    signals = {k: f[k] for k in (
        "amount", "avg_debit_90d", "amount_ratio", "beneficiary_new", "beneficiary_age_h", "beneficiary_prior_payments",
        "beneficiary_watchlisted", "device_new", "device_trusted", "device_compromised", "sim_swap_72h",
        "password_reset_24h", "otp_failures_1h", "velocity_10m", "small_cnp_merchants_15m", "drain_pct", "credits_48h",
        "distinct_senders_48h", "impossible_travel", "foreign", "away_from_home", "txn_hour", "night_share_90d", "age")}
    signals["channel"] = txn["channel"]
    signals["remarks_scam_keywords"] = any(k in remarks_raw.lower() for k in SCAM_KEYWORDS)
    signals["prior_false_positives"] = sum(1 for p in f["prior_alerts"] if "FALSE_POSITIVE" in p["disposition"])
    return {
        "alert_id": alert_id,
        "customer_ref": cust["customer_id"],
        "txn_id": txn["txn_id"],
        "alert_title": alert["title"],
        "facts": fact_list,
        "untrusted_remarks": screen["sanitised"],
        "injection_detected": screen["injection_detected"],
        "injection_patterns": screen["patterns"],
        "rule_hits": hits,
        "rule_score": score,
        "rule_band": band(score),
        "signals": signals,
        "pii_values": [cust["full_name"], cust["phone"], cust["email"], cust["pan"], f["account"]["account_number"]]
                      + ([ben["account_number"]] if ben else []) + person_names,
    }


def public_pack(pack: dict) -> dict:
    """What a model is allowed to see: the pack minus raw PII values and internal signals."""
    return {k: v for k, v in pack.items() if k not in ("pii_values", "signals")}
