#!/usr/bin/env bash
# =============================================================================
# Elder Alpha Deployment Script
# Local MicroK8s Deployment via Helm v4
#
# Usage:
#   ./scripts/deploy-alpha.sh [OPTIONS]
#
# Options:
#   --build               Build Docker images and push to local registry (default)
#   --skip-build          Skip Docker build, use existing images
#   --tag TAG             Image tag to use (default: alpha-latest)
#   --service SERVICE     Build/deploy specific service only
#   --dry-run             Show what would be deployed without applying
#   --rollback            Rollback the Helm release to the previous revision
#   --help                Show this help message
#
# Environment:
#   KUBE_CONTEXT          Kubernetes context (default: local-alpha)
#   NAMESPACE             Target namespace (default: elder)
#   APP_HOST              Web NodePort access host:port (default: localhost:30090)
#
# =============================================================================

set -euo pipefail

# =============================================================================
# Configuration
# =============================================================================

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_ROOT="$(dirname "${SCRIPT_DIR}")"

readonly APP_NAME="${APP_NAME:-elder}"
readonly KUBE_CONTEXT="${KUBE_CONTEXT:-local-alpha}"
readonly NAMESPACE="${NAMESPACE:-elder}"
readonly APP_HOST="${APP_HOST:-localhost:30090}"
readonly HELM_DIR="${HELM_DIR:-k8s/helm/elder}"
readonly VALUES_FILE="${VALUES_FILE:-k8s/helm/elder/alpha.yml}"
readonly REGISTRY="${REGISTRY:-localhost:32000}"

# Services with their build contexts and Dockerfile paths (relative to
# PROJECT_ROOT). All four Dockerfiles COPY paths relative to the repo root
# (e.g. `COPY apps/scanner/ .`), so all four need repo root as build context.
declare -A SERVICE_DOCKERFILE=(
    ["api"]="apps/api/Dockerfile"
    ["worker"]="apps/worker/Dockerfile"
    ["scanner"]="apps/scanner/Dockerfile"
    ["web"]="web/Dockerfile"
)
declare -A SERVICE_CONTEXT=(
    ["api"]="."
    ["worker"]="."
    ["scanner"]="."
    ["web"]="."
)

readonly APP_VERSION="$(cat "${PROJECT_ROOT}/.version" 2>/dev/null || echo "0.0.0.0")"

# Defaults
declare TAG="alpha-latest"
declare SERVICE_FILTER=""
declare SKIP_BUILD=false
declare DRY_RUN=false
declare DO_ROLLBACK=false

# =============================================================================
# Color output helpers
# =============================================================================

readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly NC='\033[0m'

print_info() {
    echo -e "${BLUE}[INFO]${NC} $*"
}

print_success() {
    echo -e "${GREEN}[OK]${NC} $*"
}

print_warning() {
    echo -e "${YELLOW}[WARN]${NC} $*"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $*" >&2
}

# =============================================================================
# kubectl / helm wrappers (always pass context explicitly)
# =============================================================================

kctl() {
    kubectl --context "${KUBE_CONTEXT}" "$@"
}

helm_cmd() {
    helm --kube-context "${KUBE_CONTEXT}" "$@"
}

# =============================================================================
# Prerequisite checks
# =============================================================================

check_prerequisites() {
    print_info "Checking prerequisites..."
    local missing=()

    for cmd in kubectl docker microk8s helm; do
        if ! command -v "${cmd}" &>/dev/null; then
            missing+=("${cmd}")
        fi
    done

    if [[ ${#missing[@]} -gt 0 ]]; then
        print_error "Missing required tools: ${missing[*]}"
        exit 1
    fi

    # Verify context exists
    if ! kubectl config get-contexts "${KUBE_CONTEXT}" &>/dev/null; then
        print_error "Kubernetes context '${KUBE_CONTEXT}' not found"
        echo "Available contexts:"
        kubectl config get-contexts --output=name
        exit 1
    fi

    # Verify cluster reachable
    if ! kctl cluster-info &>/dev/null; then
        print_error "Cannot reach cluster via context '${KUBE_CONTEXT}'"
        print_error "Is MicroK8s running? Try: microk8s status"
        exit 1
    fi

    # Verify Helm chart exists
    if [[ ! -f "${PROJECT_ROOT}/${HELM_DIR}/Chart.yaml" ]]; then
        print_error "Helm chart not found: ${HELM_DIR}"
        exit 1
    fi

    print_success "All prerequisites satisfied"
}

# =============================================================================
# Docker build and push to the local MicroK8s registry (localhost:32000)
# =============================================================================

build_and_push() {
    local service="$1"
    local tag="$2"
    local dockerfile="${PROJECT_ROOT}/${SERVICE_DOCKERFILE[${service}]}"
    local context="${PROJECT_ROOT}/${SERVICE_CONTEXT[${service}]}"

    if [[ ! -f "${dockerfile}" ]]; then
        print_warning "No Dockerfile found for ${service} (${dockerfile}) — skipping"
        return 0
    fi

    local image_name="${REGISTRY}/${APP_NAME}-${service}:${tag}"

    print_info "Building image: ${image_name}"
    local build_args=(--build-arg "APP_VERSION=${APP_VERSION}")
    if [[ "${service}" == "web" ]]; then
        build_args=(
            --build-arg "VITE_VERSION=${APP_VERSION}"
            --build-arg "VITE_BUILD_TIME=$(date +%s)"
            # Absolute URL required: web (NodePort 30090) and api (NodePort
            # 30091) are two different origins on localhost, not one
            # same-origin ingress host — see k8s/helm/elder/alpha.yml
            # web.buildArgs.VITE_API_URL for the source of truth this must
            # match, and apps/api/config.py _build_cors_origins for the
            # matching CORS allow.
            --build-arg "VITE_API_URL=http://localhost:30091"
        )
    fi

    if ! docker build \
        --file "${dockerfile}" \
        --tag "${image_name}" \
        --label "environment=alpha" \
        --label "timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        "${build_args[@]}" \
        "${context}"; then
        print_error "Failed to build ${service}"
        return 1
    fi

    print_info "Pushing ${image_name} to local registry..."
    if ! docker push "${image_name}"; then
        print_error "Failed to push ${image_name} — is the local registry running? " \
            "(microk8s enable registry, or: docker run -d -p 32000:5000 registry:2)"
        return 1
    fi

    print_success "Built and pushed: ${image_name}"
}

# =============================================================================
# Helm deployment
# =============================================================================

do_deploy() {
    print_info "Deploying to local MicroK8s cluster via Helm..."
    print_info "  Context:   ${KUBE_CONTEXT}"
    print_info "  Namespace: ${NAMESPACE}"
    print_info "  Chart:     ${HELM_DIR}"
    print_info "  Values:    ${VALUES_FILE}"
    print_info "  Host:      ${APP_HOST}"

    if [[ "${DRY_RUN}" == "true" ]]; then
        print_info "DRY-RUN: Rendering Helm output..."
        helm_cmd upgrade --install "${APP_NAME}" "${PROJECT_ROOT}/${HELM_DIR}" \
            --namespace "${NAMESPACE}" \
            --create-namespace \
            --values "${PROJECT_ROOT}/${VALUES_FILE}" \
            --dry-run --debug
        return 0
    fi

    if ! helm_cmd upgrade --install "${APP_NAME}" "${PROJECT_ROOT}/${HELM_DIR}" \
        --namespace "${NAMESPACE}" \
        --create-namespace \
        --values "${PROJECT_ROOT}/${VALUES_FILE}" \
        --wait --timeout 300s; then
        print_error "Failed to apply Helm release"
        return 1
    fi

    print_success "Helm release applied"
}

# =============================================================================
# Rollout verification
# =============================================================================

wait_for_rollout() {
    print_info "Waiting for deployments to roll out..."

    # Get all deployments in namespace
    local deployments
    deployments=$(kctl get deployments -n "${NAMESPACE}" -o jsonpath='{.items[*].metadata.name}' 2>/dev/null || echo "")

    if [[ -z "${deployments}" ]]; then
        print_warning "No deployments found in namespace ${NAMESPACE}"
        return 0
    fi

    local failed=false
    for deploy in ${deployments}; do
        print_info "Waiting for deployment/${deploy}..."
        if ! kctl rollout status "deployment/${deploy}" -n "${NAMESPACE}" --timeout=300s; then
            print_error "Deployment ${deploy} failed to roll out"
            failed=true
        fi
    done

    if [[ "${failed}" == "true" ]]; then
        return 1
    fi

    print_success "All workloads rolled out successfully"
}

# =============================================================================
# Show status
# =============================================================================

show_status() {
    echo ""
    print_info "Pod Status:"
    kctl get pods -n "${NAMESPACE}" -o wide
    echo ""
    print_info "Services:"
    kctl get svc -n "${NAMESPACE}"
    echo ""
    print_info "Access URL: http://${APP_HOST}"
    echo ""
    print_info "Quick commands:"
    echo "  View pods:   kubectl --context ${KUBE_CONTEXT} get pods -n ${NAMESPACE}"
    echo "  View logs:   kubectl --context ${KUBE_CONTEXT} logs -n ${NAMESPACE} -l app.kubernetes.io/instance=${APP_NAME} -f"
    echo "  Describe:    kubectl --context ${KUBE_CONTEXT} describe pods -n ${NAMESPACE}"
    echo "  Helm status: helm --kube-context ${KUBE_CONTEXT} status ${APP_NAME} -n ${NAMESPACE}"
}

# =============================================================================
# Rollback
# =============================================================================

do_rollback() {
    print_warning "Rolling back Helm release '${APP_NAME}' in ${NAMESPACE}..."

    if ! helm_cmd history "${APP_NAME}" -n "${NAMESPACE}" &>/dev/null; then
        print_error "No Helm release '${APP_NAME}' found in namespace ${NAMESPACE}"
        return 1
    fi

    helm_cmd rollback "${APP_NAME}" -n "${NAMESPACE}" --wait --timeout 300s

    print_success "Rollback complete"
    wait_for_rollout
}

# =============================================================================
# Help
# =============================================================================

show_help() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Deploy ${APP_NAME} to local MicroK8s alpha environment using Helm v4.

OPTIONS:
    --build               Build and push images to the local registry (default)
    --skip-build          Skip Docker build, use existing images
    --tag TAG             Image tag (default: alpha-latest)
    --service SERVICE     Build specific service only
    --dry-run             Render manifests without applying (helm --dry-run --debug)
    --rollback            Roll back the Helm release to the previous revision
    --help                Show this help message

ENVIRONMENT:
    KUBE_CONTEXT:   ${KUBE_CONTEXT}
    NAMESPACE:      ${NAMESPACE}
    APP_HOST:       ${APP_HOST}
    HELM_DIR:       ${HELM_DIR}
    VALUES_FILE:    ${VALUES_FILE}
    REGISTRY:       ${REGISTRY}

SERVICES:
    api        (apps/api, build context: repo root)
    worker     (apps/worker, build context: repo root)
    scanner    (apps/scanner, build context: repo root)
    web        (web, build context: repo root)

EXAMPLES:
    # Full build and deploy
    $(basename "$0")

    # Deploy without rebuilding images
    $(basename "$0") --skip-build

    # Build and deploy only one service
    $(basename "$0") --service api

    # Preview what would be applied
    $(basename "$0") --skip-build --dry-run

    # Roll back to the previous Helm revision
    $(basename "$0") --rollback
EOF
}

# =============================================================================
# Main
# =============================================================================

main() {
    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --build)
                SKIP_BUILD=false
                shift
                ;;
            --skip-build)
                SKIP_BUILD=true
                shift
                ;;
            --tag)
                TAG="$2"
                shift 2
                ;;
            --service)
                SERVICE_FILTER="$2"
                shift 2
                ;;
            --dry-run)
                DRY_RUN=true
                shift
                ;;
            --rollback)
                DO_ROLLBACK=true
                shift
                ;;
            --help)
                show_help
                exit 0
                ;;
            *)
                print_error "Unknown option: $1"
                show_help
                exit 1
                ;;
        esac
    done

    echo ""
    print_info "=========================================="
    print_info "  ${APP_NAME} — Alpha Deployment (Helm)"
    print_info "=========================================="
    echo ""

    check_prerequisites

    # Handle rollback
    if [[ "${DO_ROLLBACK}" == "true" ]]; then
        do_rollback
        show_status
        exit $?
    fi

    # Build images
    if [[ "${SKIP_BUILD}" != "true" ]]; then
        print_info "Building and pushing Docker images to ${REGISTRY}..."
        for service in "${!SERVICE_DOCKERFILE[@]}"; do
            if [[ -z "${SERVICE_FILTER}" ]] || [[ "${SERVICE_FILTER}" == "${service}" ]]; then
                build_and_push "${service}" "${TAG}" || {
                    print_error "Failed to build ${service}"
                    exit 1
                }
            fi
        done
    else
        print_info "Skipping build (--skip-build)"
    fi

    # Deploy
    do_deploy || exit 1

    if [[ "${DRY_RUN}" != "true" ]]; then
        wait_for_rollout || print_warning "Some workloads did not roll out cleanly"
        show_status
        print_success "Alpha deployment complete!"
    else
        print_success "Dry-run complete!"
    fi
}

main "$@"
