#!/bin/bash
# Deploy Elder to beta K8s cluster (dal2-beta)
# Build mode: builds amd64-only images, pushes to ghcr.io, then helm upgrade
# Rollout-only mode: helm upgrade with existing values-beta.yaml (no build)

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
    echo "Deploy Elder images to beta cluster (ghcr.io/penguintechinc + dal2-beta)"
    echo ""
    echo "IMAGE:"
    echo "  all       Build and deploy all images (default)"
    echo "  api       Build and deploy only elder-api"
    echo "  web       Build and deploy only elder-web"
    echo "  scanner   Build and deploy only elder-scanner"
    echo "  worker    Build and deploy only elder-worker"
    echo ""
    echo "OPTIONS:"
    echo "  -h, --help         Show this help message"
    echo "  -b, --build-only   Build and push images without k8s rollout"
    echo "  -r, --rollout-only Helm upgrade without building (uses values-beta.yaml)"
    echo "  -n, --namespace    Kubernetes namespace (default: elder)"
    echo "  -c, --context      Kubernetes context (default: dal2-beta)"
    echo ""
    echo "Examples:"
    echo "  $0              # Build all images and helm upgrade"
    echo "  $0 api          # Build API image and helm upgrade"
    echo "  $0 -r           # Helm upgrade only (uses values-beta.yaml image tags)"
    exit 0
}

# Configuration
REGISTRY="ghcr.io/penguintechinc"
VERSION_FILE=".version"
K8S_NAMESPACE="elder"
K8S_CONTEXT="dal2-beta"
BUILD_ONLY=false
ROLLOUT_ONLY=false
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
# Beta tag format matches CI: {version}-beta
BETA_TAG="${VERSION}-beta"

log_info "Version: $VERSION"
log_info "Beta tag: $BETA_TAG"
log_info "Target: $TARGET_IMAGE"
log_info "Namespace: $K8S_NAMESPACE"
log_info "Context: $K8S_CONTEXT"
echo ""

# Image configurations: name, dockerfile, context
declare -A IMAGES
IMAGES[api]="apps/api/Dockerfile:."
IMAGES[web]="web/Dockerfile:."
IMAGES[scanner]="apps/scanner/Dockerfile:apps/scanner"
IMAGES[worker]="apps/worker/Dockerfile:."

build_and_push_image() {
    local name=$1
    local config=${IMAGES[$name]}

    if [ -z "$config" ]; then
        log_error "Unknown image: $name"
        return 1
    fi

    IFS=':' read -r dockerfile context <<< "$config"
    local image_name="elder-${name}"
    local full_image="${REGISTRY}/${image_name}"

    log_info "Building ${image_name}..."

    # Prepare build args
    local build_args=(
        --file "$dockerfile"
        --tag "${full_image}:${BETA_TAG}"
        --tag "${full_image}:${VERSION}"
    )

    # Add service-specific build args
    if [ "$name" = "web" ]; then
        build_args+=(--build-arg "VITE_VERSION=${VERSION}")
        build_args+=(--build-arg "VITE_BUILD_TIME=$(date +%s)")

        # GitHub token for @penguintechinc packages
        if [ -f "$HOME/code/.gh-token" ]; then
            GITHUB_TOKEN=$(sed -n '4p' "$HOME/code/.gh-token")
            build_args+=(--build-arg "GITHUB_TOKEN=${GITHUB_TOKEN}")
            log_info "Using GitHub token for package authentication"
        else
            log_error "GitHub token not found at ~/code/.gh-token (required for web build)"
            return 1
        fi
    elif [ "$name" = "api" ]; then
        build_args+=(--build-arg "APP_VERSION=${VERSION}")
    fi

    docker build "${build_args[@]}" "$context"
    log_success "Built ${image_name}"

    log_info "Pushing ${image_name} to ${REGISTRY}..."
    docker push "${full_image}:${BETA_TAG}"
    docker push "${full_image}:${VERSION}"
    log_success "Pushed ${image_name} as ${BETA_TAG}"
}

helm_upgrade() {
    local set_args=()

    # If we just built specific images, override their tags via --set
    if [ "$TARGET_IMAGE" != "all" ] && [ "$ROLLOUT_ONLY" = false ]; then
        set_args+=(--set "${TARGET_IMAGE}.image.tag=${BETA_TAG}")
    fi

    log_info "Running helm upgrade..."
    helm upgrade elder k8s/helm/elder \
        --kube-context="$K8S_CONTEXT" \
        --namespace "$K8S_NAMESPACE" \
        --values k8s/helm/elder/values.yaml \
        --values k8s/helm/elder/values-beta.yaml \
        "${set_args[@]}"

    log_info "Waiting for rollout to complete..."
    local services=("api" "web" "scanner" "worker")
    if [ "$TARGET_IMAGE" != "all" ]; then
        services=("$TARGET_IMAGE")
    fi

    for svc in "${services[@]}"; do
        local deployment="elder-${svc}"
        if kubectl --context="$K8S_CONTEXT" get deployment "$deployment" -n "$K8S_NAMESPACE" &>/dev/null; then
            kubectl --context="$K8S_CONTEXT" rollout status \
                "deployment/${deployment}" \
                -n "$K8S_NAMESPACE" \
                --timeout=300s || log_warn "Rollout status check timed out for ${deployment}"
        fi
    done

    log_success "Helm upgrade complete"
}

# Determine which images to build
if [ "$TARGET_IMAGE" = "all" ]; then
    TARGETS=("api" "web" "scanner" "worker")
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

# Rollout phase via helm upgrade
if [ "$BUILD_ONLY" = false ]; then
    log_info "=== Helm Upgrade ==="
    helm_upgrade
    echo ""
fi

log_success "=== Deployment Complete ==="
echo ""
if [ "$ROLLOUT_ONLY" = false ]; then
    log_info "Deployed images:"
    for target in "${TARGETS[@]}"; do
        log_info "  - ${REGISTRY}/elder-${target}:${BETA_TAG}"
    done
    echo ""
fi
log_info "To check status:"
log_info "  kubectl --context=${K8S_CONTEXT} get pods -n ${K8S_NAMESPACE}"
log_info "  kubectl --context=${K8S_CONTEXT} logs -f deployment/elder-api -n ${K8S_NAMESPACE}"
