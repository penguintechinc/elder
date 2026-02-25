#!/bin/bash

# ==========================================================================
# K8s Beta Smoke Test Suite - Orchestrator
# Runs all beta smoke tests for the project
# ==========================================================================

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../" && pwd)"
PROJECT_NAME="$(basename "$PROJECT_DIR")"
HELM_DIR="infrastructure/helm/$PROJECT_NAME"

echo "========================================"
echo "K8s Beta Smoke Tests - $PROJECT_NAME"
echo "========================================"
echo "Project Directory: $PROJECT_DIR"
echo "Project Name: $PROJECT_NAME"
echo "Helm Directory: $HELM_DIR"
echo ""

# Change to project directory
cd "$PROJECT_DIR"

# Run each test in sequence
echo "Step 1: Deploying with Helm..."
./tests/k8s/beta/01-deploy-helm.sh || exit 1

echo ""
echo "Step 2: Waiting for services to be ready..."
./tests/k8s/beta/02-wait-ready.sh || exit 1

echo ""
echo "Step 3: Running hardcoded checks..."
./tests/k8s/beta/03-hardcoded-check.sh || exit 1

echo ""
echo "Step 4: Testing DNS resolution..."
./tests/k8s/beta/04-dns-resolution.sh || exit 1

echo ""
echo "Step 5: Running integration tests..."
./tests/k8s/beta/05-integration-test.sh || exit 1

echo ""
echo "Step 6: Testing network policies..."
./tests/k8s/beta/06-network-policy.sh || exit 1

echo ""
echo "Step 7: Running scaling tests..."
./tests/k8s/beta/07-scaling-test.sh || exit 1

echo ""
echo "Step 8: Cleanup..."
./tests/k8s/beta/08-cleanup.sh || exit 1

echo ""
echo "========================================"
echo "Beta smoke tests PASSED!"
echo "========================================"
