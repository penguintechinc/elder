#!/bin/bash

# ==========================================================================
# K8s Alpha Smoke Test Suite - Orchestrator
# Runs all alpha smoke tests for the project
# ==========================================================================

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../" && pwd)"
PROJECT_NAME="$(basename "$PROJECT_DIR")"
HELM_DIR="infrastructure/helm/$PROJECT_NAME"

echo "========================================"
echo "K8s Alpha Smoke Tests - $PROJECT_NAME"
echo "========================================"
echo "Project Directory: $PROJECT_DIR"
echo "Project Name: $PROJECT_NAME"
echo "Helm Directory: $HELM_DIR"
echo ""

# Change to project directory
cd "$PROJECT_DIR"

# Run each test in sequence
echo "Step 1: Building Docker images..."
./tests/k8s/alpha/01-build-images.sh || exit 1

echo ""
echo "Step 2: Deploying with Helm..."
./tests/k8s/alpha/02-deploy-helm.sh || exit 1

echo ""
echo "Step 3: Waiting for services to be ready..."
./tests/k8s/alpha/03-wait-ready.sh || exit 1

echo ""
echo "Step 4: Running health checks..."
./tests/k8s/alpha/04-health-check.sh || exit 1

echo ""
echo "Step 5: Running unit tests..."
./tests/k8s/alpha/05-unit-tests.sh || exit 1

echo ""
echo "Step 6: Running API integration tests..."
./tests/k8s/alpha/06-api-integration.sh || exit 1

echo ""
echo "Step 7: Testing page loads..."
./tests/k8s/alpha/07-page-load.sh || exit 1

echo ""
echo "Step 8: Cleanup..."
./tests/k8s/alpha/08-cleanup.sh || exit 1

echo ""
echo "========================================"
echo "Alpha smoke tests PASSED!"
echo "========================================"
