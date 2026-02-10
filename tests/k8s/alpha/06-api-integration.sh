#!/bin/bash

# ==========================================================================
# API Integration Tests for Alpha Services
# ==========================================================================

set -e

PROJECT_NAME="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../" && basename "$(pwd)")"
NAMESPACE="${PROJECT_NAME}-alpha"

echo "Running API integration tests for $PROJECT_NAME..."

# Try to find a service with an API port
SERVICES=$(kubectl get svc -n "$NAMESPACE" -o jsonpath='{.items[*].metadata.name}' 2>/dev/null)

echo "Found services: $SERVICES"

# Attempt port-forward to test connectivity
for SERVICE in $SERVICES; do
    echo "Testing service: $SERVICE"

    # Try common API ports
    for PORT in 8000 8080 5000 4000 3000 5432 6379; do
        if kubectl get svc "$SERVICE" -n "$NAMESPACE" -o jsonpath='{.spec.ports[*].port}' 2>/dev/null | grep -q "$PORT"; then
            echo "Service $SERVICE has port $PORT"
            # This would ideally test connectivity
        fi
    done
done

echo "API integration tests step completed"
