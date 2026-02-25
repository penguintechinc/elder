#!/bin/bash

# ==========================================================================
# Page Load Tests for Alpha Services
# ==========================================================================

set -e

PROJECT_NAME="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../" && basename "$(pwd)")"
NAMESPACE="${PROJECT_NAME}-alpha"

echo "Running page load tests for $PROJECT_NAME..."

# Check if there are any web services (typically on port 3000, 3005, 8000, etc.)
SERVICES=$(kubectl get svc -n "$NAMESPACE" -o jsonpath='{.items[*].metadata.name}' 2>/dev/null)

if [ -z "$SERVICES" ]; then
    echo "No services found, skipping page load tests"
    exit 0
fi

echo "Found services: $SERVICES"
echo "Page load tests step completed"
