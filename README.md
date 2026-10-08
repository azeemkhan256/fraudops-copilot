# FraudOps Copilot

Governed agentic AI for fraud-alert investigation.

## Hosting on Render (free)

- Language: Python 3 (version from `.python-version`)
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn app.api:app --host 0.0.0.0 --port $PORT`
- Environment variables: `GEMINI_API_KEY`, `CEREBRAS_API_KEY` (optional: `GROQ_API_KEY`, `MISTRAL_API_KEY`, ...)

The web app is at `/`, the API reference at `/docs`. With no model keys, choose `mock-heuristic`.
The database is rebuilt with demo data whenever the service starts.

## Running locally

Double-click `START_HERE.bat` in the original project folder (sets up Python, the app and optionally n8n).
