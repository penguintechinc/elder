#!/bin/bash

# ==========================================================================
# Scaling Tests for Beta Services
# ==========================================================================

set -e

PROJECT_NAME="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../" && basename "$(pwd)")"
NAMESPACE="${PROJECT_NAME}-beta"

echo "Testing scaling capabilities in namespace $NAMESPACE..."

# Check HPA (Horizontal Pod Autoscaler) resources
HPA_LIST=$(kubectl get hpa -n "$NAMESPACE" -o jsonpath='{.items[*].metadata.name}' 2>/dev/null)

if [ -z "$HPA_LIST" ]; then
    echo "No HPA resources found (autoscaling may be disabled in beta)"
else
    echo "Found HPA resources: $HPA_LIST"

    # Show HPA details
    kubectl get hpa -n "$NAMESPACE" -o wide
fi

# Check current deployment replicas
echo ""
echo "Current deployment replica counts:"
kubectl get deployment -n "$NAMESPACE" -o custom-columns=NAME:.metadata.name,REPLICAS:.spec.replicas,READY:.status.readyReplicas

echo "Scaling tests step completed"
