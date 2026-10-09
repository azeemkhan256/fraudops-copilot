#!/usr/bin/env bash
# n8n service: imports + publishes the 3 workflows (same as START_HERE.bat option 6), then starts n8n
# behind nginx. If n8n or nginx stops, the container exits so the host restarts it.
set -u
cd /home/node/svc
mkdir -p /tmp/nginx

PORT="${PORT:-10000}"
SELF_URL="${RENDER_EXTERNAL_URL:-}"
[ -z "$SELF_URL" ] && [ -n "${RAILWAY_PUBLIC_DOMAIN:-}" ] && SELF_URL="https://$RAILWAY_PUBLIC_DOMAIN"
SELF_URL="${SELF_URL:-http://localhost:$PORT}"
export WEBHOOK_URL="$SELF_URL/"
export N8N_EDITOR_BASE_URL="$SELF_URL/"

# Free hosting has 512 MB: load only the node types the 3 workflows use instead of all 400+
export NODES_INCLUDE='["n8n-nodes-base.webhook","n8n-nodes-base.httpRequest","n8n-nodes-base.if","n8n-nodes-base.manualTrigger","n8n-nodes-base.respondToWebhook","n8n-nodes-base.scheduleTrigger","n8n-nodes-base.set","n8n-nodes-base.stickyNote","n8n-nodes-base.switch"]'

# Address of the FraudOps app service. The webhooks receive it in each request (api_base);
# the SLA monitor has no request, so its built-in 127.0.0.1:8000 is replaced here.
APP_URL="${FRAUD_APP_URL:-http://127.0.0.1:8000}"
APP_URL="${APP_URL%/}"
echo "[n8n] FraudOps app: $APP_URL"

# Optional extra login in front of the editor. With both variables set, the browser asks for them;
# without them the editor is open to anyone with the link (n8n's own owner account still applies).
if [ -n "${N8N_EDITOR_USER:-}" ] && [ -n "${N8N_EDITOR_PASSWORD:-}" ]; then
  printf '%s:%s\n' "$N8N_EDITOR_USER" "$(openssl passwd -apr1 "$N8N_EDITOR_PASSWORD")" > /tmp/nginx/htpasswd
  echo "[n8n] editor login enabled for user '$N8N_EDITOR_USER'"
  sed "s#__PORT__#$PORT#" nginx.conf > /tmp/nginx/nginx.conf
else
  echo "[n8n] editor open - no extra login (set N8N_EDITOR_USER and N8N_EDITOR_PASSWORD to add one)"
  sed -e "s#__PORT__#$PORT#" -e '/auth_basic/d' nginx.conf > /tmp/nginx/nginx.conf
fi

# nginx first, so the host sees the port open while n8n prepares
nginx -e stderr -c /tmp/nginx/nginx.conf -g 'daemon off;' &

mkdir -p /tmp/workflows
for f in workflows/*.json; do
  sed "s#http://127.0.0.1:8000#$APP_URL#g" "$f" > "/tmp/workflows/${f##*/}"
done
echo "[n8n] importing the 3 workflows"
n8n import:workflow --separate --input=/tmp/workflows
for id in FraudInvOrch0001 HitlDecision0002 SlaMonitor000003; do
  echo "[n8n] publishing $id"
  n8n publish:workflow --id="$id"
done

echo "[n8n] starting n8n"
n8n start &

wait -n
echo "[n8n] a process stopped - exiting so the container restarts"
exit 1
