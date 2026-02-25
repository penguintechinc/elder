#!/bin/bash

# ==========================================================================
# Health Checks for Alpha Services
# ==========================================================================

set -e

PROJECT_NAME="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../" && basename "$(pwd)")"
NAMESPACE="${PROJECT_NAME}-alpha"

echo "Running health checks for $PROJECT_NAME..."

# Get pods in the namespace
PODS=$(kubectl get pods -n "$NAMESPACE" -o jsonpath='{.items[*].metadata.name}' 2>/dev/null)

if [ -z "$PODS" ]; then
    echo "Error: No pods found in namespace $NAMESPACE"
    exit 1
fi

echo "Found pods: $PODS"

# Check each pod's status
for POD in $PODS; do
    echo "Checking pod: $POD"
    STATUS=$(kubectl get pod "$POD" -n "$NAMESPACE" -o jsonpath='{.status.phase}')

    if [ "$STATUS" != "Running" ]; then
        echo "Warning: Pod $POD is in $STATUS state"
        kubectl describe pod "$POD" -n "$NAMESPACE"
    else
        echo "Pod $POD is Running"
    fi
done

echo "Health checks completed"
