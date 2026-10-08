"""Central configuration. Values come from environment variables (or a .env file)."""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

DB_PATH = Path(os.getenv("FRAUD_DB_PATH", ROOT / "data" / "bank.db"))
API_URL = os.getenv("FRAUD_API_URL", "http://localhost:8000")
# n8n (Node.js) resolves "localhost" to IPv6 ::1 first, while the API listens on IPv4, so n8n gets 127.0.0.1.
N8N_API_BASE = os.getenv("N8N_API_BASE", "http://127.0.0.1:8000")
N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL", "http://localhost:5678/webhook/fraud-investigate")
N8N_DECISION_WEBHOOK_URL = os.getenv(
    "N8N_DECISION_WEBHOOK_URL", "http://localhost:5678/webhook/fraud-decision"
)
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "gemma-4-26b-a4b")
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "90"))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "1"))  # schema-repair retries per agent

# --- Decision thresholds (governance-controlled; changing them should go through change control) ---
AUTO_CLOSE_MAX_RISK = int(os.getenv("AUTO_CLOSE_MAX_RISK", "30"))        # below this, A route allowed
AUTO_CLOSE_MIN_CONFIDENCE = float(os.getenv("AUTO_CLOSE_MIN_CONFIDENCE", "0.80"))
ESCALATE_MIN_RISK = int(os.getenv("ESCALATE_MIN_RISK", "70"))            # at/above this, E route
# Guardrail switch used ONLY for the benchmark's injection stress test (never disable in normal use).
INJECTION_SCREEN_ENABLED = os.getenv("INJECTION_SCREEN_ENABLED", "true").lower() != "false"
QA_SAMPLE_RATE = float(os.getenv("QA_SAMPLE_RATE", "0.10"))
# Demo speed-up: reuse a model's earlier answer to the byte-identical prompt (marked "cached" in the trace).
# The benchmark always switches this off so every measurement is a fresh model call.
LLM_CACHE_ENABLED = os.getenv("LLM_CACHE", "on").lower() not in ("off", "false", "0")
LLM_CACHE_DIR = Path(os.getenv("LLM_CACHE_DIR", ROOT / "data" / "llm_cache"))              # share of auto-closed cases sent for human QA

PROVIDERS = {
    "gemini": {  # Google AI Studio key: open-weight Gemma models (+ Gemini as optional closed reference)
        "base_url": os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"),
        "api_key_env": "GEMINI_API_KEY", "alt_env": ["GOOGLE_API_KEY"], "format": "gemini",
    },
    "cerebras": {
        "base_url": os.getenv("CEREBRAS_BASE_URL", "https://api.cerebras.ai/v1"),
        "api_key_env": "CEREBRAS_API_KEY",
    },
    "mistral": {
        "base_url": os.getenv("MISTRAL_BASE_URL", "https://api.mistral.ai/v1"),
        "api_key_env": "MISTRAL_API_KEY",
    },
    "nvidia": {
        "base_url": os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
        "api_key_env": "NVIDIA_API_KEY",
    },
    "groq": {
        "base_url": os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
        "api_key_env": "GROQ_API_KEY",
    },
    "ollama": {
        "base_url": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
        "api_key_env": None,
    },
    "openrouter": {
        "base_url": os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        "api_key_env": "OPENROUTER_API_KEY",
    },
    "together": {
        "base_url": os.getenv("TOGETHER_BASE_URL", "https://api.together.xyz/v1"),
        "api_key_env": "TOGETHER_API_KEY",
    },
    "mock": {"base_url": None, "api_key_env": None},
}

MODELS_FILE = ROOT / "models.json"


def load_models() -> list[dict]:
    """Model registry: label -> provider + model id. Edit models.json to change the line-up."""
    if MODELS_FILE.exists():
        return json.loads(MODELS_FILE.read_text(encoding="utf-8"))["models"]
    return [{"label": "mock-heuristic", "provider": "mock", "model": "mock", "family": "Offline mock"}]


def get_model(label: str) -> dict:
    for m in load_models():
        if m["label"] == label:
            return m
    raise KeyError(f"Unknown model label '{label}'. Check models.json.")
