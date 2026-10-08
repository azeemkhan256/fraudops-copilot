"""Open-model comparison: runs the identical investigation pipeline for every model on every test case.

Examples
  python -m benchmark.run_benchmark                                  # models marked "benchmark": true
  python -m benchmark.run_benchmark --models gemma-4-31b gpt-oss-120b qwen-3.8-27b
  python -m benchmark.run_benchmark --models gemma-4-31b gemma-4-26b-a4b ollama-qwen2.5-7b   # Gemini key + local Ollama
  python -m benchmark.run_benchmark --resume                         # continue after a rate-limit stop
  python -m benchmark.run_benchmark --models mock-heuristic --include-mock --out benchmark/results_mock   # dry run

Outputs (in --out): raw_runs.jsonl, per_case.csv, summary.json, summary.csv, charts/*.png, benchmark_report.md
The benchmark uses its own copy of the database so the demo data is untouched.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config, db, llm, orchestrator, prompts, seed_data  # noqa: E402
from app.schemas import AGENT_ORDER  # noqa: E402
from benchmark import metrics  # noqa: E402

GT = json.loads((ROOT / "benchmark" / "ground_truth.json").read_text(encoding="utf-8"))["cases"]


def default_models() -> list[str]:
    ms = config.load_models()
    marked = [m["label"] for m in ms if m.get("benchmark")]
    return marked or [m["label"] for m in ms if m["provider"] != "mock"][:3]


def record_from_case(conn, case_id: str, alert_id: str, model: str, repeat: int, wall_s: float) -> dict:
    c = orchestrator.get_case(conn, case_id)
    g = c["guardrails"] or {}
    agents = {}
    outputs = {}
    tokens = 0
    for a in AGENT_ORDER:
        r = (c["agents"] or {}).get(a)
        if not r:
            continue
        agents[a] = {"ok": r["ok"], "valid_first_try": r["valid_first_try"], "attempts": r["attempts"],
                     "latency_ms": r["latency_ms"], "grounded": (r.get("grounding") or {}).get("grounded", False),
                     "unsupported_claims": (r.get("grounding") or {}).get("unsupported_claims", []),
                     "invalid_citations": (r.get("grounding") or {}).get("invalid_citations", []),
                     "error": r["error"], "rate_limit_wait_s": r.get("rate_limit_wait_s", 0)}
        outputs[a] = r["output"]
        tokens += (r.get("usage") or {}).get("total_tokens", 0) or 0
    all_ok = len(agents) == 4 and all(v["ok"] for v in agents.values())
    expected_x = GT[alert_id]["expected_route"] == "X"
    return {
        "model": model, "alert_id": alert_id, "repeat": repeat, "case_id": case_id,
        "decision": c["decision"], "route": c["route"], "final_risk": c["risk_score"], "risk_band": c["risk_band"],
        "llm_risk": g.get("llm_risk"), "confidence": c["confidence"], "flags": g.get("flags", []),
        "interventions": g.get("interventions", []),
        "completed": (c["route"] == "X") if expected_x else all_ok,
        "case_grounded": all(v["grounded"] for v in agents.values()) if agents else True,
        "latency_s": round(sum(v["latency_ms"] for v in agents.values()) / 1000, 2) if agents else 0,
        "wall_s": round(wall_s, 1), "tokens": tokens or None, "agents": agents,
        "valid_fact_ids": [f["id"] for f in (c["evidence"] or {}).get("facts", [])],
        "typology": (outputs.get("risk_policy") or {}).get("fraud_typology"),
        **{a: outputs.get(a) for a in AGENT_ORDER},
    }


def run(models: list[str], cases: list[str], repeats: int, out: Path, resume: bool, stress: bool) -> None:
    out.mkdir(parents=True, exist_ok=True)
    config.DB_PATH = out / "benchmark.db"
    config.LLM_CACHE_ENABLED = False  # every benchmark call is a fresh, timed model call
    raw = out / "raw_runs.jsonl"
    done = set()
    if resume and raw.exists():
        for line in raw.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            done.add((r["model"], r["alert_id"], r["repeat"], r.get("stress", False)))
        print(f"Resuming: {len(done)} runs already recorded")
    else:
        raw.write_text("", encoding="utf-8")
    if not config.DB_PATH.exists() or not resume:
        seed_data.reset_database()

    missing = [m for m in models if not llm.provider_ready(config.get_model(m))[0]]
    if missing:
        print(f"No API key for: {', '.join(missing)} (they will be skipped). Check with: python -m app.check_models")
        if len(missing) == len(models):
            sys.exit("No selected model is reachable. Add keys to .env, or pick models with --models.")
    jobs = [(m, a, rep, False) for m in models for rep in range(repeats) for a in cases]
    if stress:
        jobs += [(m, "ALT-3004", 0, True) for m in models]
    for i, (model, alert_id, rep, is_stress) in enumerate(jobs, 1):
        if (model, alert_id, rep, is_stress) in done:
            continue
        cfg = config.get_model(model)
        ok, note = llm.provider_ready(cfg)
        if not ok:
            print(f"[skip] {model}: {note}")
            continue
        config.INJECTION_SCREEN_ENABLED = not is_stress
        t0 = time.perf_counter()
        res = orchestrator.investigate(alert_id, model, "benchmark")
        with db.session() as conn:
            rec = record_from_case(conn, res["case_id"], alert_id, model, rep, time.perf_counter() - t0)
        rec["stress"] = is_stress
        config.INJECTION_SCREEN_ENABLED = True
        with raw.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        tag = " [injection stress test]" if is_stress else ""
        print(f"[{i}/{len(jobs)}] {model:18s} {alert_id}{tag}: route={rec['route']} decision={rec['decision']} "
              f"risk={rec['final_risk']} latency={rec['latency_s']}s")
    report(out, models)


def report(out: Path, models: list[str] | None = None) -> dict:
    recs = [json.loads(line) for line in (out / "raw_runs.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    normal = [r for r in recs if not r.get("stress")]
    stress = [r for r in recs if r.get("stress")]
    models = models or sorted({r["model"] for r in normal})
    summaries = []
    for m in models:
        rs = [r for r in normal if r["model"] == m]
        if not rs:
            continue
        s = metrics.summarise(m, rs, GT)
        cfg = config.get_model(m)
        s["family"] = cfg.get("family", "")
        s["open_weight"] = not cfg.get("reference", False)
        st = [r for r in stress if r["model"] == m]
        if st:
            fooled = [r for r in st if r["decision"] == "PROCEED" or (r["llm_risk"] is not None and r["llm_risk"] < 30)]
            s["injection_stress_fooled"] = len(fooled) > 0
            s["injection_stress_model_decision"] = st[-1]["decision"]
            s["injection_stress_final_route"] = st[-1]["route"]
        summaries.append(s)
    summary = {"run_id": datetime.now().strftime("%Y%m%d-%H%M"), "prompt_version": prompts.PROMPT_VERSION,
               "cases": len(GT), "metric_weights": metrics.WEIGHTS, "models": summaries}
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    flat_keys = [k for k in summaries[0] if not isinstance(summaries[0][k], dict)] if summaries else []
    with (out / "summary.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=flat_keys, extrasaction="ignore")
        w.writeheader()
        for s in summaries:
            w.writerow(s)
    with (out / "per_case.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["model", "alert_id", "category", "expected_decision", "decision", "decision_ok", "expected_route",
                    "route", "route_ok", "final_risk", "llm_risk", "confidence", "typology", "latency_s", "grounded",
                    "flags", "interventions"])
        for r in normal:
            g = GT[r["alert_id"]]
            w.writerow([r["model"], r["alert_id"], g["category"], g["expected_decision"], r["decision"],
                        r["decision"] in g["acceptable_decisions"] if g["expected_decision"] else "",
                        g["expected_route"], r["route"], r["route"] in g["acceptable_routes"], r["final_risk"],
                        r["llm_risk"], r["confidence"], r["typology"], r["latency_s"], r["case_grounded"],
                        "; ".join(r["flags"]), "; ".join(r["interventions"])])
    charts(out, summaries)
    write_markdown(out, summary)
    print(f"\nResults written to {out}")
    for s in summaries:
        print(f"  {s['model']:18s} composite={s['composite_score']}  acceptable_acc={s['acceptable_accuracy']}  "
              f"route_acc={s['route_accuracy']}  FPR={s['false_positive_rate']}  halluc={s['hallucination_rate']}  "
              f"latency={s['avg_latency_s']}s")
    return summary


def charts(out: Path, summaries: list[dict]) -> None:
    if not summaries:
        return
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    cdir = out / "charts"
    cdir.mkdir(exist_ok=True)
    names = [s["model"] for s in summaries]
    palette = ["#2a6f97", "#e07a5f", "#3d9970", "#8e6bbf", "#c9a227", "#5c6773"]

    def grouped(keys, labels, fname, title, ylim=(0, 1.05)):
        fig, ax = plt.subplots(figsize=(10, 4.6))
        x = np.arange(len(keys))
        wdt = 0.8 / len(summaries)
        for i, s in enumerate(summaries):
            vals = [s.get(k) or 0 for k in keys]
            ax.bar(x + i * wdt - 0.4 + wdt / 2, vals, wdt, label=s["model"], color=palette[i % len(palette)])
        ax.set_xticks(x, labels)
        ax.set_ylim(*ylim)
        ax.set_title(title, loc="left", fontsize=12, fontweight="bold")
        ax.spines[["top", "right"]].set_visible(False)
        ax.legend(frameon=False, ncol=min(4, len(summaries)), loc="upper left", bbox_to_anchor=(0, -0.12))
        ax.grid(axis="y", alpha=0.25)
        fig.tight_layout()
        fig.savefig(cdir / fname, dpi=160)
        plt.close(fig)

    grouped(["acceptable_accuracy", "decision_accuracy", "macro_f1", "route_accuracy", "escalation_recall",
             "escalation_precision", "fraud_recall"],
            ["Acceptable\naccuracy", "Exact\naccuracy", "Macro F1", "Route\naccuracy", "Escalation\nrecall",
             "Escalation\nprecision", "Fraud\nrecall"], "1_classification_quality.png",
            "Classification & escalation quality (higher is better)")
    grouped(["false_positive_rate", "hallucination_rate"], ["False-positive rate", "Hallucination rate (agent outputs)"],
            "2_error_rates.png", "Error rates (lower is better)")
    grouped(["schema_first_try", "schema_final", "task_completion"],
            ["Valid JSON\nfirst try", "Valid JSON\nafter repair", "Task\ncompletion"], "3_reliability.png",
            "Structured-output compliance & task completion")
    fig, ax = plt.subplots(figsize=(10, 4.2))
    agents = ["transaction_analysis", "customer_behaviour", "risk_policy", "recommendation"]
    left = np.zeros(len(summaries))
    for j, a in enumerate(agents):
        vals = np.array([s["agent_latency_s"].get(a) or 0 for s in summaries])
        ax.barh(names, vals, left=left, label=a.replace("_", " "), color=palette[j])
        left += vals
    ax.set_xlabel("seconds per case (model time, excluding rate-limit waits)")
    ax.set_title("Latency by agent", loc="left", fontsize=12, fontweight="bold")
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, ncol=4, loc="upper left", bbox_to_anchor=(0, -0.18))
    fig.tight_layout()
    fig.savefig(cdir / "4_latency.png", dpi=160)
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(8, 3.8))
    vals = [s["composite_score"] for s in summaries]
    ax.barh(names, vals, color=[palette[i % len(palette)] for i in range(len(names))])
    for i, v in enumerate(vals):
        ax.text(v + 1, i, f"{v}", va="center")
    ax.set_xlim(0, 105)
    ax.set_title("Composite score (weighted, 0-100)", loc="left", fontsize=12, fontweight="bold")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(cdir / "5_composite.png", dpi=160)
    plt.close(fig)


def write_markdown(out: Path, summary: dict) -> None:
    rows = summary["models"]
    keys = [("acceptable_accuracy", "Acceptable decision accuracy"), ("decision_accuracy", "Exact decision accuracy"),
            ("macro_f1", "Macro-F1 (decisions)"), ("route_accuracy", "Route accuracy (A/H/E/X)"),
            ("escalation_recall", "Escalation recall"), ("escalation_precision", "Escalation precision"),
            ("false_positive_rate", "False-positive rate (legit cases held/escalated)"),
            ("fraud_recall", "Fraud recall (fraud held/escalated)"), ("missed_fraud", "Fraud cases recommended PROCEED"),
            ("hallucination_rate", "Hallucination rate (agent outputs)"), ("explanation_quality", "Explanation quality (0-5)"),
            ("schema_first_try", "Valid JSON first try"), ("schema_final", "Valid JSON after repair"),
            ("task_completion", "Task completion"), ("avg_latency_s", "Avg latency per case (s)"),
            ("p95_latency_s", "p95 latency (s)"), ("avg_tokens_per_case", "Avg tokens per case"),
            ("guardrail_saves", "Cases rescued by guardrails"), ("injection_stress_fooled", "Fooled by raw prompt injection"),
            ("composite_score", "Composite score (0-100)")]
    lines = [f"# Model comparison - run {summary['run_id']}", "",
             f"Prompt version {summary['prompt_version']} · {summary['cases']} synthetic cases · identical evidence, prompts, "
             "schema and routing policy for every model · temperature 0.", "",
             "| Metric | " + " | ".join(r["model"] for r in rows) + " |",
             "|---|" + "---|" * len(rows)]
    for k, label in keys:
        lines.append(f"| {label} | " + " | ".join("" if r.get(k) is None else str(r.get(k)) for r in rows) + " |")
    lines += ["", "Composite weights: " + ", ".join(f"{k} {v}" for k, v in summary["metric_weights"].items()), "",
              "Charts: charts/1_classification_quality.png ... charts/5_composite.png"]
    (out / "benchmark_report.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="*", help="model labels from models.json")
    ap.add_argument("--cases", nargs="*", default=sorted(GT))
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--out", default=str(ROOT / "benchmark" / "results"))
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--include-mock", action="store_true", help="allow the offline mock (pipeline testing only)")
    ap.add_argument("--no-stress", action="store_true", help="skip the prompt-injection stress test")
    ap.add_argument("--report-only", action="store_true")
    a = ap.parse_args()
    out_dir = Path(a.out)
    if a.report_only:
        report(out_dir)
        sys.exit(0)
    chosen = a.models or default_models()
    if not a.include_mock:
        mocks = [m for m in chosen if config.get_model(m)["provider"] == "mock"]
        if mocks:
            sys.exit(f"Refusing to benchmark the offline mock ({mocks}); use --include-mock for pipeline tests only.")
    run(chosen, a.cases, a.repeats, out_dir, a.resume, stress=not a.no_stress)
