#!/usr/bin/env bash
# FraudOps Copilot - full install on one always-on Ubuntu 24.04 server (Oracle Cloud Always Free).
# Runs everything as on the local PC: API + web app, Streamlit, n8n with the 3 workflows, project website.
# Public HTTPS links via Caddy and sslip.io:
#   https://fraudops.<ip-with-dashes>.sslip.io   web app (/, /docs, /streamlit/, /docs-site/)
#   https://n8n.<ip-with-dashes>.sslip.io        n8n editor
# Usage (on the server):  curl -fsSL https://raw.githubusercontent.com/azeemkhan256/fraudops-copilot/main/deploy/oracle/install.sh | sudo bash
# Safe to run again (it updates the code and restarts everything).
set -euo pipefail

REPO="${REPO:-https://github.com/azeemkhan256/fraudops-copilot.git}"
APP=/opt/fraudops
RUN_USER="${SUDO_USER:-ubuntu}"
say() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

[ "$(id -u)" = 0 ] || { echo "Run with sudo"; exit 1; }

say "Public IP"
IP="$(curl -fsS https://api.ipify.org)"
HOST_IP="${IP//./-}"
APP_HOST="fraudops.$HOST_IP.sslip.io"
N8N_HOST="n8n.$HOST_IP.sslip.io"
echo "$IP -> https://$APP_HOST  and  https://$N8N_HOST"

say "Swap (helps on small servers)"
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q /swapfile /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

say "System packages (Python, Docker, Caddy, git)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip git docker.io caddy iptables-persistent curl
systemctl enable --now docker

say "Firewall: open ports 80 and 443 on the server"
for p in 80 443; do
  iptables -C INPUT -p tcp --dport $p -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -p tcp --dport $p -j ACCEPT
done
netfilter-persistent save

say "App code"
if [ -d "$APP/.git" ]; then
  git -C "$APP" pull --ff-only
else
  git clone "$REPO" "$APP"
fi
chown -R "$RUN_USER:$RUN_USER" "$APP"

say "API keys (stored only on this server in $APP/.env)"
if [ ! -f "$APP/.env" ]; then
  read -rsp "Paste GEMINI_API_KEY (input hidden, Enter to skip): " GEMINI </dev/tty; echo
  read -rsp "Paste CEREBRAS_API_KEY (input hidden, Enter to skip): " CEREBRAS </dev/tty; echo
  umask 077
  cat > "$APP/.env" <<EOF
GEMINI_API_KEY=$GEMINI
CEREBRAS_API_KEY=$CEREBRAS
DEFAULT_MODEL=gemma-4-26b-a4b
LLM_CACHE=on
FRAUD_API_URL=http://127.0.0.1:8000
N8N_API_BASE=http://127.0.0.1:8000
N8N_WEBHOOK_URL=http://127.0.0.1:5678/webhook/fraud-investigate
N8N_DECISION_WEBHOOK_URL=http://127.0.0.1:5678/webhook/fraud-decision
EOF
  chown "$RUN_USER:$RUN_USER" "$APP/.env"
else
  echo "$APP/.env already exists - keeping the keys in it"
fi

say "Python environment"
sudo -u "$RUN_USER" python3 -m venv "$APP/.venv"
sudo -u "$RUN_USER" "$APP/.venv/bin/pip" install -q --upgrade pip
sudo -u "$RUN_USER" "$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"

say "Services: API + web app, Streamlit"
cat > /etc/systemd/system/fraudops-api.service <<EOF
[Unit]
Description=FraudOps API + web app
After=network-online.target
[Service]
User=$RUN_USER
WorkingDirectory=$APP
Environment=MPLCONFIGDIR=/tmp/matplotlib-$RUN_USER
ExecStart=$APP/.venv/bin/uvicorn app.api:app --host 127.0.0.1 --port 8000 --no-access-log
Restart=always
[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/fraudops-streamlit.service <<EOF
[Unit]
Description=FraudOps Streamlit screen
After=fraudops-api.service
[Service]
User=$RUN_USER
WorkingDirectory=$APP
Environment=MPLCONFIGDIR=/tmp/matplotlib-$RUN_USER
ExecStart=$APP/.venv/bin/streamlit run ui/streamlit_app.py --server.address 127.0.0.1 --server.port 8501 --server.baseUrlPath streamlit --server.headless true
Restart=always
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable fraudops-api fraudops-streamlit
systemctl restart fraudops-api fraudops-streamlit

say "n8n with the 3 workflows (data kept on disk, so your owner account survives restarts)"
N8N_IMAGE=docker.n8n.io/n8nio/n8n:2
docker pull -q "$N8N_IMAGE"
docker rm -f n8n >/dev/null 2>&1 || true
docker volume create n8n_data >/dev/null
N8N_RUN=(docker run --rm --network host -v n8n_data:/home/node/.n8n -v "$APP/n8n:/workflows:ro" "$N8N_IMAGE")
"${N8N_RUN[@]}" import:workflow --separate --input=/workflows
for id in FraudInvOrch0001 HitlDecision0002 SlaMonitor000003; do
  "${N8N_RUN[@]}" publish:workflow --id="$id" || echo "WARNING: could not publish $id - publish it in the n8n editor"
done
docker run -d --name n8n --restart unless-stopped --network host \
  -v n8n_data:/home/node/.n8n \
  -e N8N_LISTEN_ADDRESS=127.0.0.1 -e N8N_PORT=5678 -e N8N_PROXY_HOPS=1 \
  -e N8N_HOST="$N8N_HOST" -e N8N_PROTOCOL=https \
  -e WEBHOOK_URL="https://$N8N_HOST/" -e N8N_EDITOR_BASE_URL="https://$N8N_HOST/" \
  -e N8N_DIAGNOSTICS_ENABLED=false -e N8N_VERSION_NOTIFICATIONS_ENABLED=false \
  -e N8N_PERSONALIZATION_ENABLED=false -e GENERIC_TIMEZONE=Asia/Kolkata \
  -e EXECUTIONS_DATA_PRUNE=true -e EXECUTIONS_DATA_MAX_AGE=168 \
  "$N8N_IMAGE" start

say "HTTPS links (Caddy)"
cat > /etc/caddy/Caddyfile <<EOF
$APP_HOST {
	handle /streamlit* {
		reverse_proxy 127.0.0.1:8501
	}
	handle_path /docs-site/* {
		root * $APP/docs/website/site
		file_server
	}
	handle {
		reverse_proxy 127.0.0.1:8000
	}
}

$N8N_HOST {
	reverse_proxy 127.0.0.1:5678
}
EOF
chmod o+rx /opt "$APP" "$APP/docs" "$APP/docs/website" 2>/dev/null || true
systemctl enable caddy
systemctl restart caddy

say "Waiting for everything to start"
for i in $(seq 1 60); do
  curl -fs -o /dev/null http://127.0.0.1:5678/healthz && curl -fs -o /dev/null http://127.0.0.1:8000/health && break
  sleep 5
done
echo "API:        $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health)"
echo "Streamlit:  $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8501/streamlit/)"
echo "n8n:        $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5678/healthz)"

cat <<EOF

=====================================================================
 Done. Your links (HTTPS may take 1-2 minutes the first time):
   App:         https://$APP_HOST
   API docs:    https://$APP_HOST/docs
   Streamlit:   https://$APP_HOST/streamlit/
   Website:     https://$APP_HOST/docs-site/
   n8n editor:  https://$N8N_HOST   <- open it NOW and create the owner account
=====================================================================
EOF
