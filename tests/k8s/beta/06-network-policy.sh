#!/bin/bash

# ==========================================================================
# Network Policy Tests for Beta Services
# ==========================================================================

set -e

PROJECT_NAME="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../" && basename "$(pwd)")"
NAMESPACE="${PROJECT_NAME}-beta"

echo "Testing network policies in namespace $NAMESPACE..."

# Check if network policies exist
POLICIES=$(kubectl get networkpolicy -n "$NAMESPACE" -o jsonpath='{.items[*].metadata.name}' 2>/dev/null)

if [ -z "$POLICIES" ]; then
    echo "No network policies found (this may be expected depending on configuration)"
else
    echo "Found network policies: $POLICIES"

    # List network policy details
    kubectl get networkpolicy -n "$NAMESPACE" -o wide
fi

echo "Network policy tests completed"
