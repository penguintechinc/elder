#!/bin/bash

# ==========================================================================
# Wait for Services to be Ready
# ==========================================================================

set -e

PROJECT_NAME="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../" && basename "$(pwd)")"
NAMESPACE="${PROJECT_NAME}-beta"
TIMEOUT=300
ELAPSED=0

echo "Waiting for services to be ready in namespace: $NAMESPACE..."

# Wait for all deployments to be ready
while [ $ELAPSED -lt $TIMEOUT ]; do
    READY=$(kubectl get deployment -n "$NAMESPACE" -o jsonpath='{.items[*].status.conditions[?(@.type=="Available")].status}' 2>/dev/null | grep -o "True" | wc -l)
    TOTAL=$(kubectl get deployment -n "$NAMESPACE" -o jsonpath='{.items[*].metadata.name}' 2>/dev/null | wc -w)

    if [ "$TOTAL" -gt 0 ] && [ "$READY" -eq "$TOTAL" ]; then
        echo "All deployments are ready"
        break
    fi

    echo "Waiting for deployments... ($READY/$TOTAL ready, ${ELAPSED}s elapsed)"
    sleep 5
    ELAPSED=$((ELAPSED + 5))
done

if [ $ELAPSED -ge $TIMEOUT ]; then
    echo "Warning: Timeout waiting for services to be ready"
    kubectl get pods -n "$NAMESPACE"
    exit 1
fi

echo "All services are ready"
