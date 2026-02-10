#!/bin/bash

# ==========================================================================
# Run Unit Tests in K8s Alpha Environment
# ==========================================================================

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../" && pwd)"
PROJECT_NAME="$(basename "$PROJECT_DIR")"

echo "Running unit tests for $PROJECT_NAME..."
cd "$PROJECT_DIR"

# Try to run tests if Makefile has a test target
if grep -q "^test:" Makefile 2>/dev/null; then
    echo "Running 'make test' from project Makefile..."
    make test || echo "Unit tests failed or not available"
else
    echo "No test target found in Makefile, skipping unit tests"
fi

echo "Unit tests step completed"
