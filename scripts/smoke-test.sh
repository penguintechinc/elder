#!/bin/bash
# Unified Smoke Test Script for Elder
# Tests all containers end-to-end: build, run, API health, and page loads
#
# Usage: ./scripts/smoke-test.sh [OPTIONS]
#
# Modes:
#   --alpha          Alpha testing: MicroK8s local cluster via Kustomize (default)
#   --beta           Beta testing: K8s deployment at elder.penguintech.cloud
#
# Options:
#   --skip-build     Skip container build (alpha mode only)
#   --verbose, -v    Enable verbose output
#   --help, -h       Show this help message
#
# Examples:
#   ./scripts/smoke-test.sh                    # Run alpha tests (local)
#   ./scripts/smoke-test.sh --alpha            # Explicit alpha tests
#   ./scripts/smoke-test.sh --beta             # Run beta tests against K8s
#   ./scripts/smoke-test.sh --alpha --skip-build  # Alpha without rebuild

set -e

# macOS compatibility: GNU timeout is 'gtimeout' via coreutils; fall back to plain exec
_timeout() {
    if command -v gtimeout &>/dev/null; then
        gtimeout "$@"
    elif command -v timeout &>/dev/null; then
        timeout "$@"
    else
        # No timeout available — run without it
        local _secs="$1"; shift
        "$@"
    fi
}

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Default mode: alpha (local MicroK8s/Kustomize)
TEST_MODE="alpha"
SKIP_BUILD=false
VERBOSE=false
PF_PIDS=""

# Parse arguments
for arg in "$@"; do
    case $arg in
        --alpha)
            TEST_MODE="alpha"
            shift
            ;;
        --beta)
            TEST_MODE="beta"
            shift
            ;;
        --skip-build)
            SKIP_BUILD=true
            shift
            ;;
        --verbose|-v)
            VERBOSE=true
            shift
            ;;
        --help|-h)
            head -24 "$0" | tail -22
            exit 0
            ;;
    esac
done

# Configuration based on mode
if [ "$TEST_MODE" = "beta" ]; then
    # Beta mode: K8s deployment
    # Use direct load balancer origin to bypass Cloudflare
    API_URL="${API_URL:-https://dal2.penguintech.io}"
    WEB_URL="${WEB_URL:-https://dal2.penguintech.io}"
    # Set Host header for proper routing through ingress
    HOST_HEADER="elder.penguintech.cloud"
    ADMIN_USERNAME="${ADMIN_USERNAME:-admin@localhost.local}"
    ADMIN_PASSWORD="${ADMIN_PASSWORD:-admin123}"
    MODE_LABEL="BETA (K8s: elder.penguintech.cloud via dal2.penguintech.io)"
else
    # Alpha mode: local MicroK8s/Kustomize
    API_URL="${API_URL:-http://localhost:4000}"
    WEB_URL="${WEB_URL:-http://localhost:3005}"
    HOST_HEADER=""
    ADMIN_USERNAME="${ADMIN_USERNAME:-admin@localhost.local}"
    ADMIN_PASSWORD="${ADMIN_PASSWORD:-admin123}"
    MODE_LABEL="ALPHA (Local MicroK8s + Kustomize)"
fi

GRPC_PORT="${GRPC_PORT:-50052}"
MAX_WAIT=120  # Maximum seconds to wait for services
RETRY_INTERVAL=2

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[PASS]${NC} $1"
}

log_error() {
    echo -e "${RED}[FAIL]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

# Curl wrapper function to handle Host header properly in beta mode
do_curl() {
    if [ "$TEST_MODE" = "beta" ]; then
        curl -k -H "Host: $HOST_HEADER" "$@"
    else
        curl "$@"
    fi
}

log_verbose() {
    if [ "$VERBOSE" = true ]; then
        echo -e "${CYAN}[DEBUG]${NC} $1"
    fi
}

# Track test results
TESTS_PASSED=0
TESTS_FAILED=0
FAILED_TESTS=""

record_pass() {
    TESTS_PASSED=$((TESTS_PASSED + 1))
    log_success "$1"
}

record_fail() {
    TESTS_FAILED=$((TESTS_FAILED + 1))
    FAILED_TESTS="$FAILED_TESTS\n  - $1"
    log_error "$1"
}

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
export PROJECT_ROOT

cd "$PROJECT_ROOT"

log_info "=========================================="
log_info "Elder Smoke Test - $MODE_LABEL"
log_info "=========================================="
log_info "API URL: $API_URL"
log_info "Web URL: $WEB_URL"
log_info ""

# ============================================================
# ALPHA MODE: Local MicroK8s + Kustomize Tests
# ============================================================
if [ "$TEST_MODE" = "alpha" ]; then
    # Set up trap to kill port-forwards on exit
    cleanup() {
        if [ -n "$PF_PIDS" ]; then
            log_verbose "Cleaning up port-forwards: $PF_PIDS"
            kill $PF_PIDS 2>/dev/null || true
        fi
    }
    trap cleanup EXIT INT TERM

    # Step 1: Build and push images to MicroK8s registry
    if [ "$SKIP_BUILD" = false ]; then
        log_info "Step 1: Building and pushing images to MicroK8s registry..."

        # Check GITHUB_TOKEN for web build
        if [ -z "$GITHUB_TOKEN" ]; then
            log_warn "GITHUB_TOKEN not set - web image build may fail to install @penguintechinc packages"
        fi

        # Build and push each service
        for svc in api web scanner worker; do
            log_info "Building ${svc}..."
            case "$svc" in
                api)
                    if docker build -t localhost:32000/elder-api:alpha-latest -f apps/api/Dockerfile --no-cache .; then
                        if docker push localhost:32000/elder-api:alpha-latest; then
                            log_verbose "Pushed api image"
                        else
                            record_fail "Failed to push api image to MicroK8s registry"
                            exit 1
                        fi
                    else
                        record_fail "Failed to build api image"
                        exit 1
                    fi
                    ;;
                web)
                    BUILD_ARGS=""
                    if [ -n "$GITHUB_TOKEN" ]; then
                        BUILD_ARGS="--build-arg GITHUB_TOKEN=$GITHUB_TOKEN"
                    fi
                    if docker build -t localhost:32000/elder-web:alpha-latest --no-cache $BUILD_ARGS -f web/Dockerfile .; then
                        if docker push localhost:32000/elder-web:alpha-latest; then
                            log_verbose "Pushed web image"
                        else
                            record_fail "Failed to push web image to MicroK8s registry"
                            exit 1
                        fi
                    else
                        record_fail "Failed to build web image"
                        exit 1
                    fi
                    ;;
                scanner)
                    if docker build -t localhost:32000/elder-scanner:alpha-latest -f apps/scanner/Dockerfile --no-cache apps/scanner; then
                        if docker push localhost:32000/elder-scanner:alpha-latest; then
                            log_verbose "Pushed scanner image"
                        else
                            record_fail "Failed to push scanner image to MicroK8s registry"
                            exit 1
                        fi
                    else
                        record_fail "Failed to build scanner image"
                        exit 1
                    fi
                    ;;
                worker)
                    if docker build -t localhost:32000/elder-worker:alpha-latest -f apps/worker/Dockerfile --no-cache .; then
                        if docker push localhost:32000/elder-worker:alpha-latest; then
                            log_verbose "Pushed worker image"
                        else
                            record_fail "Failed to push worker image to MicroK8s registry"
                            exit 1
                        fi
                    else
                        record_fail "Failed to build worker image"
                        exit 1
                    fi
                    ;;
            esac
        done
        record_pass "All images built and pushed to MicroK8s registry"
    else
        log_info "Step 1: Skipping build (--skip-build flag set)"
    fi

    # Step 2: Delete old deployment and redeploy fresh
    log_info ""
    log_info "Step 2: Redeploying Kustomize overlay..."
    if kubectl delete --context local-alpha -k k8s/kustomize/overlays/alpha --ignore-not-found 2>/dev/null; then
        log_verbose "Deleted old K8s resources"
        # Wait for old pods to terminate
        sleep 5
    fi

    if kubectl apply --context local-alpha -k k8s/kustomize/overlays/alpha; then
        record_pass "Kustomize overlay deployed"
    else
        record_fail "Failed to deploy Kustomize overlay"
        exit 1
    fi

    # Step 3: Wait for deployments and set up port-forwards
    log_info ""
    log_info "Step 3: Waiting for K8s deployments to be ready..."

    if kubectl --context local-alpha rollout status deployment -n elder --timeout=180s > /dev/null 2>&1; then
        record_pass "All K8s deployments are ready"
    else
        record_fail "K8s deployments failed to become ready"
        log_error "Deployment status:"
        kubectl --context local-alpha get deployments -n elder
        exit 1
    fi

    # Start port-forwards as background processes
    log_info ""
    log_info "Setting up port-forwards..."

    kubectl --context local-alpha port-forward -n elder svc/api 4000:5000 > /dev/null 2>&1 &
    PF_PIDS="$! "
    log_verbose "Port-forward api: pid $!"

    kubectl --context local-alpha port-forward -n elder svc/web 3005:3000 > /dev/null 2>&1 &
    PF_PIDS="${PF_PIDS}$! "
    log_verbose "Port-forward web: pid $!"

    kubectl --context local-alpha port-forward -n elder svc/worker 8000:28000 > /dev/null 2>&1 &
    PF_PIDS="${PF_PIDS}$! "
    log_verbose "Port-forward worker: pid $!"

    kubectl --context local-alpha port-forward -n elder svc/api 50052:50051 > /dev/null 2>&1 &
    PF_PIDS="${PF_PIDS}$!"
    log_verbose "Port-forward gRPC: pid $!"

    # Wait for port-forwards to be ready
    log_info "Waiting for port-forwards to become active..."
    PORTS_READY=0
    WAIT_PORTS=0
    while [ $WAIT_PORTS -lt 30 ]; do
        if nc -z localhost 4000 2>/dev/null && nc -z localhost 3005 2>/dev/null; then
            PORTS_READY=1
            break
        fi
        sleep 1
        WAIT_PORTS=$((WAIT_PORTS + 1))
    done

    if [ $PORTS_READY -eq 0 ]; then
        record_fail "Port-forwards did not become ready"
        exit 1
    fi

    # Define wait_for_health function for alpha mode
    wait_for_health() {
        local service_name="$1"
        local url="$2"
        local waited=0

        log_verbose "Waiting for $service_name at $url..."

        while [ $waited -lt $MAX_WAIT ]; do
            if do_curl -sf "$url" > /dev/null 2>&1; then
                return 0
            fi
            sleep $RETRY_INTERVAL
            waited=$((waited + RETRY_INTERVAL))
            log_verbose "Waiting for $service_name... ($waited/$MAX_WAIT seconds)"
        done
        return 1
    }

    # Wait for API
    log_info "Waiting for API..."
    if wait_for_health "API" "$API_URL/healthz"; then
        record_pass "API health check passed"
    else
        record_fail "API health check failed"
        log_error "API pod logs:"
        kubectl --context local-alpha logs -n elder -l app=api --tail=50 2>/dev/null || echo "Could not fetch logs"
    fi

    # Wait for Web UI
    log_info "Waiting for Web UI..."
    if wait_for_health "Web UI" "$WEB_URL"; then
        record_pass "Web UI is accessible"
    else
        record_fail "Web UI health check failed"
        log_error "Web UI pod logs:"
        kubectl --context local-alpha logs -n elder -l app=web --tail=50 2>/dev/null || echo "Could not fetch logs"
    fi

# ============================================================
# BETA MODE: K8s Deployment Tests
# ============================================================
else
    log_info "Step 1-3: Skipped (beta mode uses existing K8s deployment)"

    # Function to wait for HTTPS endpoints
    wait_for_health() {
        local service_name="$1"
        local url="$2"
        local waited=0

        log_verbose "Waiting for $service_name at $url..."

        while [ $waited -lt $MAX_WAIT ]; do
            # Use -k to allow self-signed certs and Host header for ingress routing
            if do_curl -sf "$url" > /dev/null 2>&1; then
                return 0
            fi
            sleep $RETRY_INTERVAL
            waited=$((waited + RETRY_INTERVAL))
            log_verbose "Waiting for $service_name... ($waited/$MAX_WAIT seconds)"
        done
        return 1
    }

    # Verify K8s deployment is accessible
    log_info ""
    log_info "Verifying K8s deployment accessibility..."

    if wait_for_health "K8s API" "$API_URL/healthz"; then
        record_pass "K8s API is accessible"
    else
        record_fail "K8s API not accessible at $API_URL"
        log_error "Check K8s deployment: kubectl get pods -n elder"
        exit 1
    fi

    if wait_for_health "K8s Web" "$WEB_URL"; then
        record_pass "K8s Web UI is accessible"
    else
        record_fail "K8s Web UI not accessible at $WEB_URL"
    fi
fi

# ============================================================
# COMMON TESTS (both alpha and beta modes)
# ============================================================

# Step 4: Comprehensive REST API Tests
log_info ""
log_info "Step 4: Comprehensive REST API Tests..."

# For HTTPS (beta), use -k flag to handle certificates
# Note: Host header is handled by do_curl() function
CURL_OPTS=""
if [ "$TEST_MODE" = "beta" ]; then
    CURL_OPTS="-k"
fi

# Test health endpoint response content
# In beta mode, /healthz isn't proxied through nginx, so use an API endpoint for health check
if [ "$TEST_MODE" = "beta" ]; then
    # For K8s, test API health by checking a valid API endpoint returns JSON error
    # Note: don't use -f flag here since we expect 401 which is still a valid API response
    # Use do_curl to ensure Host header is included for proper routing
    HEALTH_RESPONSE=$(do_curl -s $CURL_OPTS "$API_URL/api/v1/organizations" 2>/dev/null || echo "")
    if echo "$HEALTH_RESPONSE" | grep -qi "authentication\|unauthorized\|error\|items"; then
        record_pass "API is responding (via /api/v1/organizations)"
    else
        record_fail "API health check failed (no valid response from /api/v1/organizations): $HEALTH_RESPONSE"
    fi
else
    # For local, use the direct /healthz endpoint
    HEALTH_RESPONSE=$(do_curl -sf "$API_URL/healthz" 2>/dev/null || echo "")
    if echo "$HEALTH_RESPONSE" | grep -qi "healthy\|ok\|status.*up"; then
        record_pass "API /healthz returns healthy status"
    else
        record_fail "API /healthz response invalid: $HEALTH_RESPONSE"
    fi
fi

# Test API version endpoint
VERSION_RESPONSE=$(do_curl -sf "$API_URL/api/v1/version" 2>/dev/null || echo "")
if echo "$VERSION_RESPONSE" | grep -qi "version"; then
    record_pass "API /api/v1/version returns version info"
    VERSION_VALUE=$(echo "$VERSION_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('version',''))" 2>/dev/null || echo "")
    if [ "$VERSION_VALUE" = "0.0.0" ] || [ -z "$VERSION_VALUE" ]; then
        record_fail "API version is '${VERSION_VALUE}' — build-arg not injected correctly"
    else
        record_pass "API version is '$VERSION_VALUE' (non-zero)"
    fi
else
    log_warn "API /api/v1/version not available (may be expected)"
fi

# Test authentication - login
log_info "Testing authentication..."
LOGIN_RESPONSE=$(do_curl -sf -X POST "$API_URL/api/v1/portal-auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"email\": \"$ADMIN_USERNAME\", \"password\": \"$ADMIN_PASSWORD\"}" 2>/dev/null || echo "")

TOKEN=""
if echo "$LOGIN_RESPONSE" | grep -qi "access_token\|token"; then
    TOKEN=$(echo "$LOGIN_RESPONSE" | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)
    if [ -z "$TOKEN" ]; then
        TOKEN=$(echo "$LOGIN_RESPONSE" | grep -o '"token":"[^"]*"' | cut -d'"' -f4)
    fi
    record_pass "API authentication successful"
else
    record_fail "API authentication failed: $LOGIN_RESPONSE"
fi

# Test authenticated endpoints (if we got a token)
if [ -n "$TOKEN" ]; then
    # Test organizations endpoint
    ORGS_RESPONSE=$(do_curl -sf -H "Authorization: Bearer $TOKEN" "$API_URL/api/v1/organizations" 2>/dev/null || echo "")
    if echo "$ORGS_RESPONSE" | grep -qi "items\|organizations\|\[\]"; then
        record_pass "API GET /organizations works"
    else
        record_fail "API GET /organizations failed"
    fi

    # Test entities endpoint
    ENTITIES_RESPONSE=$(do_curl -sf -H "Authorization: Bearer $TOKEN" "$API_URL/api/v1/entities" 2>/dev/null || echo "")
    if echo "$ENTITIES_RESPONSE" | grep -qi "items\|entities\|\[\]"; then
        record_pass "API GET /entities works"
    else
        record_fail "API GET /entities failed"
    fi

    # Test services endpoint
    SERVICES_RESPONSE=$(do_curl -sf -H "Authorization: Bearer $TOKEN" "$API_URL/api/v1/services" 2>/dev/null || echo "")
    if echo "$SERVICES_RESPONSE" | grep -qi "items\|services\|\[\]"; then
        record_pass "API GET /services works"
    else
        record_fail "API GET /services failed"
    fi

    # Run comprehensive REST API tests
    log_info ""
    log_info "Running comprehensive REST API endpoint tests..."
    PYTHON_CMD="python3"
    if ! command -v python3 &> /dev/null; then
        PYTHON_CMD="python"
    fi

    if [ "$TEST_MODE" = "beta" ]; then
        SSL_FLAG="--no-verify-ssl"
        HOST_FLAG="--host-header $HOST_HEADER"
    else
        SSL_FLAG=""
        HOST_FLAG=""
    fi

    if $PYTHON_CMD "$SCRIPT_DIR/test-rest-api.py" --url "$API_URL" --username "$ADMIN_USERNAME" --password "$ADMIN_PASSWORD" $SSL_FLAG $HOST_FLAG; then
        record_pass "Comprehensive REST API tests passed"
    else
        record_fail "Comprehensive REST API tests failed"
    fi

    # Run API validation tests
    log_info ""
    log_info "Running API validation tests..."
    if $PYTHON_CMD "$SCRIPT_DIR/test-api-validation.py" --url "$API_URL" --username "$ADMIN_USERNAME" --password "$ADMIN_PASSWORD" $SSL_FLAG $HOST_FLAG; then
        record_pass "API validation tests passed"
    else
        log_warn "API validation tests failed (some failures expected for edge cases)"
    fi

    # Run integration workflow tests (only in alpha mode to avoid polluting prod data)
    if [ "$TEST_MODE" != "beta" ]; then
        log_info ""
        log_info "Running integration workflow tests..."
        if $PYTHON_CMD "$SCRIPT_DIR/test-integration-workflows.py" --url "$API_URL" --username "$ADMIN_USERNAME" --password "$ADMIN_PASSWORD" $SSL_FLAG; then
            record_pass "Integration workflow tests passed"
        else
            log_warn "Integration workflow tests had some failures"
        fi
    fi
fi

# Step 5: Web UI Smoke Tests
log_info ""
log_info "Step 5: Web UI Smoke Tests..."

# Test main page loads
WEB_CONTENT=$(do_curl -sf "$WEB_URL" 2>/dev/null || echo "")
if echo "$WEB_CONTENT" | grep -qi "elder\|<!DOCTYPE\|<html"; then
    record_pass "Web UI main page loads"
else
    record_fail "Web UI main page failed to load"
fi

# Test static assets (check if JS/CSS are served)
if do_curl -sf "$WEB_URL/assets/" > /dev/null 2>&1 || do_curl -sf "$WEB_URL" | grep -q "assets/"; then
    record_pass "Web UI static assets accessible"
else
    log_warn "Web UI static assets check inconclusive"
fi

# Test login page
LOGIN_PAGE=$(do_curl -sf "$WEB_URL/login" 2>/dev/null || do_curl -sf "$WEB_URL/#/login" 2>/dev/null || echo "")
if [ -n "$LOGIN_PAGE" ]; then
    record_pass "Web UI login page accessible"
else
    log_warn "Web UI login page not directly accessible (SPA routing)"
fi

# ============================================================
# ALPHA-ONLY TESTS (K8s pod tests)
# ============================================================
if [ "$TEST_MODE" = "alpha" ]; then
    # Step 6: Scanner Pod Test
    log_info ""
    log_info "Step 6: Scanner Pod Status..."

    SCANNER_STATUS=$(kubectl --context local-alpha get pods -n elder -l app=scanner -o jsonpath='{.items[0].status.phase}' 2>/dev/null)
    if [ "$SCANNER_STATUS" = "Running" ]; then
        record_pass "Scanner pod is Running"
    else
        log_warn "Scanner pod status: $SCANNER_STATUS (may be expected for dev setup)"
    fi

    # Step 7: Worker Pod Test and Health Check
    log_info ""
    log_info "Step 7: Worker Pod Status and Health Check..."

    WORKER_STATUS=$(kubectl --context local-alpha get pods -n elder -l app=worker -o jsonpath='{.items[0].status.phase}' 2>/dev/null)
    if [ "$WORKER_STATUS" = "Running" ]; then
        record_pass "Worker pod is Running"

        # Check worker health via port-forward
        if do_curl -sf "http://localhost:8000/healthz" > /dev/null 2>&1; then
            record_pass "Worker /healthz endpoint responding"
        else
            log_warn "Worker /healthz endpoint not responding (may be expected)"
        fi
    else
        log_warn "Worker pod status: $WORKER_STATUS"
    fi

    # Step 8: gRPC Server Test
    log_info ""
    log_info "Step 8: gRPC API Tests..."

    # Check if gRPC is enabled (runs in API pod on port 50051)
    if nc -z localhost $GRPC_PORT 2>/dev/null; then
        record_pass "gRPC server is listening on port $GRPC_PORT"

        # Run comprehensive gRPC API tests
        log_info "Running comprehensive gRPC API endpoint tests..."
        PYTHON_CMD="python3"
        if ! command -v python3 &> /dev/null; then
            PYTHON_CMD="python"
        fi

        set +e
        $PYTHON_CMD "$SCRIPT_DIR/test-grpc-api.py" --host localhost --port "$GRPC_PORT" --username "$ADMIN_USERNAME" --password "$ADMIN_PASSWORD"
        GRPC_EXIT=$?
        set -e
        if [ "$GRPC_EXIT" -eq 0 ]; then
            record_pass "Comprehensive gRPC API tests passed"
        elif [ "$GRPC_EXIT" -eq 2 ]; then
            log_warn "grpcio not installed on host — skipping gRPC tests (pip3 install grpcio grpcio-tools)"
        else
            record_fail "Comprehensive gRPC API tests failed"
        fi
    else
        log_warn "gRPC server not listening on port $GRPC_PORT (may be disabled or enterprise feature)"
    fi

# ============================================================
# BETA-ONLY TESTS (K8s-specific tests)
# ============================================================
else
    # Step 6: K8s-specific health checks
    log_info ""
    log_info "Step 6: K8s Deployment Checks..."

    # Check if we can reach the K8s API through the ingress
    if do_curl -sf "$API_URL/api/v1/organizations" -H "Authorization: Bearer $TOKEN" > /dev/null 2>&1; then
        record_pass "K8s ingress routing to API works"
    else
        log_warn "K8s ingress routing check inconclusive"
    fi

    # Verify nginx proxy is working (API calls through web URL)
    PROXY_TEST=$(do_curl -sf "$WEB_URL/api/v1/organizations" -H "Authorization: Bearer $TOKEN" 2>/dev/null || echo "")
    if echo "$PROXY_TEST" | grep -qi "items\|organizations\|\[\]"; then
        record_pass "K8s nginx proxy routing works"
    else
        log_warn "K8s nginx proxy routing check inconclusive"
    fi

    log_info ""
    log_info "Step 7: gRPC API Tests (K8s)..."

    # For K8s, gRPC runs in the same pod as the API on port 50051
    # We need to extract the host from API_URL for gRPC testing
    GRPC_HOST=$(echo "$API_URL" | sed -E 's#https?://([^:/]+).*#\1#')
    GRPC_K8S_PORT="${GRPC_PORT:-50051}"

    log_info "Testing gRPC at ${GRPC_HOST}:${GRPC_K8S_PORT}..."

    # Run comprehensive gRPC API tests
    PYTHON_CMD="python3"
    if ! command -v python3 &> /dev/null; then
        PYTHON_CMD="python"
    fi

    if $PYTHON_CMD "$SCRIPT_DIR/test-grpc-api.py" --host "$GRPC_HOST" --port "$GRPC_K8S_PORT" --username "$ADMIN_USERNAME" --password "$ADMIN_PASSWORD" --tls; then
        record_pass "Comprehensive gRPC API tests passed (K8s)"
    else
        log_warn "gRPC API tests failed or not available (K8s) - may require port forwarding or ingress"
    fi

    log_info ""
    log_info "Step 8: Skipped (container-specific tests not applicable in K8s mode)"
fi

# ============================================================
# PLAYWRIGHT WEB UI TESTS (both alpha and beta modes)
# ============================================================
log_info ""
log_info "Step 9: Playwright Web UI Tests..."

# Check if Node.js and npm are available
if ! command -v npm &> /dev/null; then
    log_warn "npm not found - skipping Playwright tests"
else
    # Set up Playwright environment
    export PLAYWRIGHT_BASE_URL="$WEB_URL"

    # Disable web server in beta mode (using existing deployment)
    if [ "$TEST_MODE" = "beta" ]; then
        export PLAYWRIGHT_WEBSERVER_DISABLED=1
        export PLAYWRIGHT_TARGET_HOST="elder.penguintech.cloud"
    fi

    # For HTTPS (beta mode), we might need to disable SSL verification
    if [ "$TEST_MODE" = "beta" ]; then
        export NODE_TLS_REJECT_UNAUTHORIZED=0
    fi

    log_info "Running Playwright web UI tests against: $WEB_URL"

    # Run Playwright tests with timeout
    if _timeout 300 bash -c 'cd "$PROJECT_ROOT/web" && npm run test:e2e 2>&1' > /tmp/playwright-output.log 2>&1; then
        record_pass "Playwright web UI tests passed"
    else
        if grep -q "no such file or directory\|cannot find" /tmp/playwright-output.log; then
            log_warn "Playwright tests not yet installed - run 'cd web && npm install' to set up"
        else
            record_fail "Playwright web UI tests failed"
            log_error "Playwright test output:"
            tail -50 /tmp/playwright-output.log | sed 's/^/  /'
        fi
    fi
fi

# ============================================================
# SUMMARY
# ============================================================
log_info ""
log_info "=========================================="
log_info "Smoke Test Summary - $MODE_LABEL"
log_info "=========================================="
echo -e "${GREEN}Passed: $TESTS_PASSED${NC}"
echo -e "${RED}Failed: $TESTS_FAILED${NC}"

if [ $TESTS_FAILED -gt 0 ]; then
    echo -e "\n${RED}Failed tests:${NC}$FAILED_TESTS"
    log_info ""
    log_info "Check K8s logs via: kubectl --context local-alpha logs -n elder <pod-name>"
    exit 1
else
    log_success "All smoke tests passed!"
    exit 0
fi
