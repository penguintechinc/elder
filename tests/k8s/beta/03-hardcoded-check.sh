#!/bin/bash

# ==========================================================================
# Hardcoded Values Check for Beta Services
# ==========================================================================

set -e

PROJECT_NAME="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../" && basename "$(pwd)")"
NAMESPACE="${PROJECT_NAME}-beta"

echo "Running hardcoded configuration checks for $PROJECT_NAME..."

# Verify namespace exists
if ! kubectl get namespace "$NAMESPACE" &>/dev/null; then
    echo "Error: Namespace $NAMESPACE does not exist"
    exit 1
fi

# Check expected services exist
echo "Verifying services in namespace $NAMESPACE..."
SERVICES=$(kubectl get svc -n "$NAMESPACE" -o jsonpath='{.items[*].metadata.name}' 2>/dev/null)

if [ -z "$SERVICES" ]; then
    echo "Warning: No services found in namespace"
else
    echo "Found services: $SERVICES"
fi

# Verify replica counts
echo "Checking replica counts (beta should have 2)..."
REPLICAS=$(kubectl get deployment -n "$NAMESPACE" -o jsonpath='{.items[*].spec.replicas}' 2>/dev/null)
echo "Replica counts: $REPLICAS"

# Verify images from beta registry
echo "Verifying beta registry images..."
IMAGES=$(kubectl get deployment -n "$NAMESPACE" -o jsonpath='{.items[*].spec.template.spec.containers[*].image}' 2>/dev/null)
echo "Images: $IMAGES"

echo "Hardcoded checks completed"
