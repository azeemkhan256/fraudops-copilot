# FraudOps Copilot - n8n service (Render: Docker, Dockerfile path deploy/n8n/Dockerfile, build context = repo root).
# Runs n8n 2.x with the 3 workflows on 127.0.0.1:5678 behind nginx on the public port:
#   /webhook/* and /healthz are open (the app calls them); the editor needs a login.
FROM node:24-bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends nginx openssl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Same major version the local launcher installs
RUN npm install -g n8n@2 --no-audit --no-fund && npm cache clean --force

# uid 1000 = "node" user in this image
USER node
ENV HOME=/home/node \
    NODE_OPTIONS=--max-old-space-size=384 \
    N8N_USER_FOLDER=/home/node/n8n-data \
    N8N_LISTEN_ADDRESS=127.0.0.1 \
    N8N_PORT=5678 \
    N8N_PROXY_HOPS=1 \
    N8N_PUSH_BACKEND=sse \
    N8N_DIAGNOSTICS_ENABLED=false \
    N8N_VERSION_NOTIFICATIONS_ENABLED=false \
    N8N_PERSONALIZATION_ENABLED=false

WORKDIR /home/node/svc
COPY --chown=node n8n/*.json workflows/
COPY --chown=node deploy/n8n/start.sh deploy/n8n/nginx.conf ./
RUN mkdir -p /home/node/n8n-data && sed -i 's/\r$//' start.sh && chmod +x start.sh

EXPOSE 10000
CMD ["bash", "start.sh"]
