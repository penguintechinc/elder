#!/bin/bash
# Deploy Elder to registry-dal2.penguintech.io and update beta k8s cluster
# Builds amd64-only images, pushes to private registry, and triggers rollout

set -e

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

usage() {
    echo "Usage: $0 [OPTIONS] [IMAGE]"
    echo ""
    echo "Deploy Elder images to beta cluster (registry-dal2.penguintech.io)"
    echo ""
    echo "IMAGE:"
    echo "  all       Build and deploy all images (default)"
    echo "  api       Build and deploy only elder-api"
    echo "  web       Build and deploy only elder-web"
    echo "  scanner   Build and deploy only elder-scanner"
    echo "  worker    Build and deploy only elder-worker"
    echo ""
    echo "OPTIONS:"
    echo "  -h, --help       Show this help message"
    echo "  -b, --build-only Build and push images without k8s rollout"
    echo "  -r, --rollout-only Trigger k8s rollout without building (use existing images)"
    echo "  --restart        Force pod restart after rollout (ensures new images load)"
    echo "  -n, --namespace  Kubernetes namespace (default: elder)"
    echo "  -c, --context    Kubernetes context (default: dal2-beta)"
    echo ""
    echo "Examples:"
    echo "  $0              # Build and deploy all images"
    echo "  $0 api          # Build and deploy only API"
    echo "  $0 web          # Build and deploy only Web"
    echo "  $0 -r           # Rollout all deployments without rebuilding"
    echo "  $0 -r api       # Rollout only API deployment"
    exit 0
}

# Configuration
REGISTRY="registry-dal2.penguintech.io"
VERSION_FILE=".version"
K8S_NAMESPACE="elder"
K8S_CONTEXT="dal2-beta"
BUILD_ONLY=false
ROLLOUT_ONLY=false
FORCE_RESTART=false
TARGET_IMAGE="all"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help)
            usage
            ;;
        -b|--build-only)
            BUILD_ONLY=true
            shift
            ;;
        -r|--rollout-only)
            ROLLOUT_ONLY=true
            shift
            ;;
        --restart)
            FORCE_RESTART=true
            shift
            ;;
        -n|--namespace)
            K8S_NAMESPACE="$2"
            shift 2
            ;;
        -c|--context)
            K8S_CONTEXT="$2"
            shift 2
            ;;
        all|api|web|scanner|worker)
            TARGET_IMAGE="$1"
            shift
            ;;
        *)
            log_error "Unknown option: $1"
            usage
            ;;
    esac
done

# Get script directory and project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

# Read version from .version file
if [ ! -f "$VERSION_FILE" ]; then
    log_error "Version file not found: $VERSION_FILE"
    exit 1
fi

VERSION=$(cat "$VERSION_FILE" | tr -d '\n')
log_info "Version: $VERSION"
log_info "Target: $TARGET_IMAGE"
log_info "Namespace: $K8S_NAMESPACE"
log_info "Context: $K8S_CONTEXT"
echo ""

# Image configurations: name, dockerfile, context, deployment_name, container_name
declare -A IMAGES
IMAGES[api]="apps/api/Dockerfile:.:elder-api:api"
IMAGES[web]="web/Dockerfile:.:elder-web:web"
IMAGES[scanner]="apps/scanner/Dockerfile:apps/scanner:elder-scanner:scanner"
IMAGES[worker]="apps/worker/Dockerfile:.:elder-worker:worker"

build_and_push_image() {
    local name=$1
    local config=${IMAGES[$name]}

    if [ -z "$config" ]; then
        log_error "Unknown image: $name"
        return 1
    fi

    IFS=':' read -r dockerfile context _ _ <<< "$config"
    local image_name="elder-${name}"

    log_info "Building ${image_name}..."

    # Prepare build args
    local build_args=(
        --file "$dockerfile"
        --tag "${REGISTRY}/${image_name}:${VERSION}"
        --tag "${REGISTRY}/${image_name}:latest"
    )

    # Add service-specific build args
    if [ "$name" = "web" ]; then
        # Web-specific: Vite env vars
        build_args+=(--build-arg "VITE_VERSION=${VERSION}")
        build_args+=(--build-arg "VITE_BUILD_TIME=$(date +%s)")

        # GitHub token for @penguintechinc packages
        if [ -f "$HOME/code/.gh-token" ]; then
            GITHUB_TOKEN=$(cat "$HOME/code/.gh-token" | grep -v '^#' | head -1)
            build_args+=(--build-arg "GITHUB_TOKEN=${GITHUB_TOKEN}")
            log_info "Using GitHub token for package authentication"
        else
            log_error "GitHub token not found at ~/code/.gh-token (required for web build)"
            return 1
        fi
    elif [ "$name" = "api" ]; then
        # API-specific: Flask app version
        build_args+=(--build-arg "APP_VERSION=${VERSION}")
    fi

    docker build "${build_args[@]}" "$context"

    log_success "Built ${image_name}"

    log_info "Pushing ${image_name} to ${REGISTRY}..."
    docker push "${REGISTRY}/${image_name}:${VERSION}"
    docker push "${REGISTRY}/${image_name}:latest"

    log_success "Pushed ${image_name}"
}

rollout_deployment() {
    local name=$1
    local config=${IMAGES[$name]}

    if [ -z "$config" ]; then
        log_error "Unknown image: $name"
        return 1
    fi

    IFS=':' read -r _ _ deployment_name container_name <<< "$config"
    local image_name="elder-${name}"

    log_info "Updating deployment ${deployment_name}..."

    # Check if deployment exists
    if ! kubectl --context="$K8S_CONTEXT" get deployment "$deployment_name" -n "$K8S_NAMESPACE" &>/dev/null; then
        log_warn "Deployment ${deployment_name} not found in namespace ${K8S_NAMESPACE}, skipping"
        return 0
    fi

    # Update image and trigger rollout
    kubectl --context="$K8S_CONTEXT" set image \
        "deployment/${deployment_name}" \
        "${container_name}=${REGISTRY}/${image_name}:${VERSION}" \
        -n "$K8S_NAMESPACE"

    log_info "Waiting for rollout to complete..."
    kubectl --context="$K8S_CONTEXT" rollout status \
        "deployment/${deployment_name}" \
        -n "$K8S_NAMESPACE" \
        --timeout=300s

    # Force restart if requested (ensures image cache is cleared)
    if [ "$FORCE_RESTART" = true ]; then
        log_info "Force restarting deployment ${deployment_name}..."
        kubectl --context="$K8S_CONTEXT" rollout restart \
            "deployment/${deployment_name}" \
            -n "$K8S_NAMESPACE"

        log_info "Waiting for restart to complete..."
        kubectl --context="$K8S_CONTEXT" rollout status \
            "deployment/${deployment_name}" \
            -n "$K8S_NAMESPACE" \
            --timeout=300s
    fi

    log_success "Deployment ${deployment_name} rolled out successfully"
}

# Determine which images to process
if [ "$TARGET_IMAGE" = "all" ]; then
    TARGETS=("api" "web")  # Default to api and web for 'all'
else
    TARGETS=("$TARGET_IMAGE")
fi

# Build and push phase
if [ "$ROLLOUT_ONLY" = false ]; then
    log_info "=== Building and Pushing Images ==="
    for target in "${TARGETS[@]}"; do
        build_and_push_image "$target"
        echo ""
    done
fi

# Rollout phase
if [ "$BUILD_ONLY" = false ]; then
    log_info "=== Updating Kubernetes Deployments ==="
    for target in "${TARGETS[@]}"; do
        rollout_deployment "$target"
        echo ""
    done
fi

log_success "=== Deployment Complete ==="
echo ""
log_info "Deployed images:"
for target in "${TARGETS[@]}"; do
    log_info "  - ${REGISTRY}/elder-${target}:${VERSION}"
done
echo ""
log_info "To check status:"
log_info "  kubectl --context=${K8S_CONTEXT} get pods -n ${K8S_NAMESPACE}"
log_info "  kubectl --context=${K8S_CONTEXT} logs -f deployment/elder-api -n ${K8S_NAMESPACE}"
