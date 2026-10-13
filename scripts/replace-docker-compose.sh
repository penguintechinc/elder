#!/bin/bash
# Replace docker-compose v1 with a wrapper for docker compose v2

set -euo pipefail

echo "Removing old docker-compose v1..."
sudo rm -f /usr/bin/docker-compose

echo "Creating docker-compose wrapper for docker compose v2..."
sudo tee /usr/local/bin/docker-compose > /dev/null << 'EOF'
#!/bin/bash
# Wrapper to redirect docker-compose v1 calls to docker compose v2
exec docker compose "$@"
EOF

sudo chmod +x /usr/local/bin/docker-compose

echo "Done! docker-compose now wraps docker compose v2"
docker-compose --version
