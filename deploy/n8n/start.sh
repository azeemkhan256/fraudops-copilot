#!/usr/bin/env bash
# n8n service: imports + publishes the 3 workflows (same as START_HERE.bat option 6), then starts n8n
# behind nginx. If n8n or nginx stops, the container exits so the host restarts it.
set -u
cd /home/node/svc
mkdir -p /tmp/nginx

PORT="${PORT:-10000}"
SELF_URL="${RENDER_EXTERNAL_URL:-http://localhost:$PORT}"
export WEBHOOK_URL="$SELF_URL/"
export N8N_EDITOR_BASE_URL="$SELF_URL/"

# Address of the FraudOps app service. The webhooks receive it in each request (api_base);
# the SLA monitor has no request, so its built-in 127.0.0.1:8000 is replaced here.
APP_URL="${FRAUD_APP_URL:-http://127.0.0.1:8000}"
APP_URL="${APP_URL%/}"
echo "[n8n] FraudOps app: $APP_URL"

# Editor login. Without both variables the editor stays locked (webhooks still work).
if [ -n "${N8N_EDITOR_USER:-}" ] && [ -n "${N8N_EDITOR_PASSWORD:-}" ]; then
  printf '%s:%s\n' "$N8N_EDITOR_USER" "$(openssl passwd -apr1 "$N8N_EDITOR_PASSWORD")" > /tmp/nginx/htpasswd
  echo "[n8n] editor login enabled for user '$N8N_EDITOR_USER'"
else
  printf 'locked:%s\n' "$(openssl passwd -apr1 "$(openssl rand -hex 24)")" > /tmp/nginx/htpasswd
  echo "[n8n] editor LOCKED - set N8N_EDITOR_USER and N8N_EDITOR_PASSWORD to open it"
fi

# nginx first, so the host sees the port open while n8n prepares
sed "s#__PORT__#$PORT#" nginx.conf > /tmp/nginx/nginx.conf
nginx -e stderr -c /tmp/nginx/nginx.conf -g 'daemon off;' &

mkdir -p /tmp/workflows
for wf in 01_fraud_investigation_orchestrator.json:FraudInvOrch0001 \
          02_hitl_decision_handler.json:HitlDecision0002 \
          03_sla_escalation_monitor.json:SlaMonitor000003; do
  f="${wf%%:*}"; id="${wf##*:}"
  sed "s#http://127.0.0.1:8000#$APP_URL#g" "workflows/$f" > "/tmp/workflows/$f"
  echo "[n8n] importing $f"
  n8n import:workflow --input="/tmp/workflows/$f" && n8n publish:workflow --id="$id"
done

echo "[n8n] starting n8n"
n8n start &

wait -n
echo "[n8n] a process stopped - exiting so the container restarts"
exit 1
