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

## n8n service (second free Render web service)

- New Web Service → same repository → Language **Docker**
- Dockerfile Path: `deploy/n8n/Dockerfile`, Docker Build Context Directory: `.` (repo root)
- Environment variables: `FRAUD_APP_URL` (the app's link, e.g. `https://fraudops-copilot.onrender.com`),
  `N8N_EDITOR_USER`, `N8N_EDITOR_PASSWORD` (login for the n8n editor)
- The 3 workflows are imported and published on every start.

Then add to the **app** service's environment variables (replace with the n8n service link):
`N8N_WEBHOOK_URL=https://<n8n-link>/webhook/fraud-investigate`,
`N8N_DECISION_WEBHOOK_URL=https://<n8n-link>/webhook/fraud-decision`,
`N8N_API_BASE=https://<app-link>`
