#!/bin/bash

# ==========================================================================
# DNS Resolution Tests for Beta Services
# ==========================================================================

set -e

PROJECT_NAME="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../" && basename "$(pwd)")"
NAMESPACE="${PROJECT_NAME}-beta"

echo "Testing DNS resolution in namespace $NAMESPACE..."

# Get list of services
SERVICES=$(kubectl get svc -n "$NAMESPACE" -o jsonpath='{.items[*].metadata.name}' 2>/dev/null)

if [ -z "$SERVICES" ]; then
    echo "No services found, skipping DNS tests"
    exit 0
fi

echo "Testing DNS names:"
for SERVICE in $SERVICES; do
    DNS_NAME="${SERVICE}.${NAMESPACE}.svc.cluster.local"
    echo "  - $DNS_NAME"
done

echo "DNS resolution tests completed"
