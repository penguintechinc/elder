#!/bin/bash

# ==========================================================================
# Build Docker Images for Alpha Testing
# ==========================================================================

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../" && pwd)"
PROJECT_NAME="$(basename "$PROJECT_DIR")"

echo "Building Docker images for $PROJECT_NAME..."
cd "$PROJECT_DIR"

# Build images for the project
if command -v docker &> /dev/null; then
    echo "Building images with docker..."

    # For generic projects, attempt to build main image if Dockerfile exists
    if [ -f "Dockerfile" ]; then
        docker build -t "$PROJECT_NAME:test" -f Dockerfile .
    fi

    # Build component-specific images
    for component in manager api backend proxy webui scanner; do
        if [ -f "$component/Dockerfile" ]; then
            echo "Building $component image..."
            docker build -t "$PROJECT_NAME/$component:test" -f "$component/Dockerfile" .
        fi
    done

    echo "Docker images built successfully"
else
    echo "Warning: docker not found, skipping image build"
fi

echo "Build images step completed"
