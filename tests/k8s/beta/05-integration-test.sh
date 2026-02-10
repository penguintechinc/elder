#!/bin/bash

# ==========================================================================
# Integration Tests for Beta Services
# ==========================================================================

set -e

PROJECT_NAME="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../" && basename "$(pwd)")"
NAMESPACE="${PROJECT_NAME}-beta"

echo "Running integration tests for $PROJECT_NAME in beta environment..."

# Check all pods are running
PODS=$(kubectl get pods -n "$NAMESPACE" -o jsonpath='{.items[*].metadata.name}' 2>/dev/null)

if [ -z "$PODS" ]; then
    echo "Error: No pods found in namespace $NAMESPACE"
    exit 1
fi

RUNNING_COUNT=$(kubectl get pods -n "$NAMESPACE" -o jsonpath='{.items[?(@.status.phase=="Running")].metadata.name}' 2>/dev/null | wc -w)
TOTAL_COUNT=$(echo "$PODS" | wc -w)

echo "Pods running: $RUNNING_COUNT / $TOTAL_COUNT"

if [ "$RUNNING_COUNT" -lt "$TOTAL_COUNT" ]; then
    echo "Warning: Not all pods are running"
    kubectl get pods -n "$NAMESPACE"
fi

echo "Integration tests step completed"
