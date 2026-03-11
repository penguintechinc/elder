#!/bin/bash
# E2E Test Script for Elder - Alpha (Local Kubernetes)
#
# Runs the full E2E suite against the local MicroK8s alpha cluster:
#   1. Pod health and readiness (smoke)
#   2. API endpoint suite (all routes)
#   3. Playwright browser tests (web UI)
#
# Prerequisites:
#   - MicroK8s running with context local-alpha
#   - elder.localhost.local in /etc/hosts pointing to 127.0.0.1
#   - Deployment already applied (use deploy-alpha.sh if needed)
#
# Usage: ./scripts/e2e-test-alpha.sh [OPTIONS]
#
# Options:
#   --deploy     Deploy to K8s before testing
#   --verbose    Enable verbose output
#   --help       Show this help message

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Configuration
CONTEXT="local-alpha"
NAMESPACE="elder"
BASE_URL="http://elder.localhost.local"
DEPLOY=false
VERBOSE=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --deploy) DEPLOY=true; shift ;;
        --verbose) VERBOSE=true; shift ;;
        --help)
            sed -n '/^# Usage/,/^$/p' "$0" | grep -v '^$'
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

log_info()    { echo -e "${BLUE}[INFO]${NC}  $1"; }
log_success() { echo -e "${GREEN}[OK]${NC}    $1"; }
log_error()   { echo -e "${RED}[FAIL]${NC}  $1"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC}  $1"; }
log_section() {
    echo ""
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

PHASE_RESULTS=()

echo ""
echo -e "${BLUE}================================================${NC}"
echo -e "${BLUE}  Elder E2E Tests - Alpha (Local K8s)${NC}"
echo -e "${BLUE}================================================${NC}"
echo ""
log_info "Context:   $CONTEXT"
log_info "Namespace: $NAMESPACE"
log_info "Base URL:  $BASE_URL"
echo ""

###############################################################################
# PHASE 1: Deploy (optional)
###############################################################################
if [ "$DEPLOY" = true ]; then
    log_section "Phase 1: Deploy"
    log_info "Deploying via Kustomize..."
    kubectl apply --context "$CONTEXT" -k "$PROJECT_ROOT/k8s/kustomize/overlays/alpha"
    log_info "Waiting 60s for pods to settle..."
    sleep 60
    log_success "Deployed"
fi

###############################################################################
# PHASE 2: Smoke — pod health
###############################################################################
log_section "Phase 2: Pod Health"

if ! kubectl config get-contexts 2>/dev/null | grep -q "$CONTEXT"; then
    log_error "K8s context '$CONTEXT' not found"
    exit 1
fi

POD_JSON=$(kubectl --context "$CONTEXT" get pods -n "$NAMESPACE" -o json 2>/dev/null || echo "{}")
if [ "$POD_JSON" = "{}" ]; then
    log_error "No pods found in namespace '$NAMESPACE'. Run with --deploy or deploy manually."
    exit 1
fi

RUNNING=$(echo "$POD_JSON" | jq -r '.items[] | select(.status.phase=="Running") | .metadata.name' 2>/dev/null | wc -l)
TOTAL=$(echo "$POD_JSON" | jq -r '.items[].metadata.name' 2>/dev/null | wc -l)
READY=$(echo "$POD_JSON" | jq -r '.items[] | select(.status.conditions[]? | select(.type=="Ready" and .status=="True")) | .metadata.name' 2>/dev/null | wc -l)

log_info "Pods: $RUNNING/$TOTAL running, $READY/$TOTAL ready"

if [ "$RUNNING" -eq 0 ]; then
    log_error "No running pods"
    kubectl --context "$CONTEXT" get pods -n "$NAMESPACE" -o wide
    exit 1
fi

log_success "Pods healthy ($RUNNING running)"
PHASE_RESULTS+=("Pod Health: PASS ($RUNNING/$TOTAL running)")

###############################################################################
# PHASE 3: Smoke — ingress reachability
###############################################################################
log_section "Phase 3: Ingress Reachability"

if ! grep -q "elder.localhost.local" /etc/hosts 2>/dev/null; then
    log_warn "elder.localhost.local not in /etc/hosts — add: echo '127.0.0.1 elder.localhost.local' | sudo tee -a /etc/hosts"
fi

WEB_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/" 2>/dev/null || echo "000")
API_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/healthz" 2>/dev/null || echo "000")

if [ "$WEB_CODE" != "200" ]; then
    log_error "WebUI not reachable via ingress (HTTP $WEB_CODE) — $BASE_URL/"
    PHASE_RESULTS+=("Ingress: FAIL (web $WEB_CODE)")
    exit 1
fi

if [ "$API_CODE" != "200" ]; then
    log_error "API not reachable via ingress (HTTP $API_CODE) — $BASE_URL/healthz"
    PHASE_RESULTS+=("Ingress: FAIL (api $API_CODE)")
    exit 1
fi

log_success "Web ($WEB_CODE) and API ($API_CODE) reachable via ingress"
PHASE_RESULTS+=("Ingress: PASS")

###############################################################################
# PHASE 4: API endpoint suite
###############################################################################
log_section "Phase 4: API Endpoint Suite"

EMAIL="admin@localhost.local"
PASSWORD="admin123"
TENANT_ID=1
API_BASE="$BASE_URL/api/v1"

PASSED=0
FAILED=0
FAILED_ENDPOINTS=()

api_call() {
    local method=$1
    local endpoint=$2
    local data=${3:-}
    if [[ -n "$data" ]]; then
        timeout 10 curl -s -w "\n%{http_code}" -X "$method" "$API_BASE$endpoint" \
            -H "Authorization: Bearer $TOKEN" \
            -H "Content-Type: application/json" \
            -d "$data" 2>/dev/null
    else
        timeout 10 curl -s -w "\n%{http_code}" -X "$method" "$API_BASE$endpoint" \
            -H "Authorization: Bearer $TOKEN" \
            -H "Content-Type: application/json" 2>/dev/null
    fi
}

check_response() {
    local response=$1
    local expected_codes=$2
    local endpoint=$3
    local method=$4

    local http_code
    http_code=$(echo "$response" | tail -n1)
    local body
    body=$(echo "$response" | sed '$d')

    local found=0
    IFS=',' read -ra CODES <<< "$expected_codes"
    for code in "${CODES[@]}"; do
        [[ "$http_code" == "$code" ]] && found=1 && break
    done

    if [[ $found -eq 1 ]]; then
        [[ "$VERBOSE" == "true" ]] && echo -e "  ${GREEN}✓${NC} [$method] $endpoint (HTTP $http_code)"
        PASSED=$((PASSED + 1))
    else
        local err
        err=$(echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('error','') or d.get('message',''))" 2>/dev/null || echo "${body:0:80}")
        echo -e "  ${RED}✗${NC} [$method] $endpoint — HTTP $http_code $err"
        FAILED=$((FAILED + 1))
        FAILED_ENDPOINTS+=("[$method] $endpoint — HTTP $http_code")
    fi
}

# Authenticate
log_info "Authenticating as $EMAIL..."
LOGIN=$(timeout 10 curl -s -X POST "$API_BASE/portal-auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"tenant_id\": $TENANT_ID, \"email\": \"$EMAIL\", \"password\": \"$PASSWORD\"}" 2>/dev/null)
TOKEN=$(echo "$LOGIN" | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null)

if [[ -z "$TOKEN" ]]; then
    log_error "Authentication failed — cannot run API suite"
    echo "$LOGIN"
    PHASE_RESULTS+=("API Suite: FAIL (auth)")
    exit 1
fi
log_success "Authenticated"

# Fetch IDs for downstream tests
ORG_RESP=$(api_call GET "/organizations")
ORG_ID=$(echo "$ORG_RESP" | sed '$d' | python3 -c "import sys,json; d=json.load(sys.stdin); items=d.get('items',[]); print(items[0]['id'] if items else 1)" 2>/dev/null || echo "1")

ENTITY_RESP=$(api_call GET "/entities")
ENTITY_ID=$(echo "$ENTITY_RESP" | sed '$d' | python3 -c "import sys,json; d=json.load(sys.stdin); items=d.get('items',[]); print(items[0]['id'] if items else 0)" 2>/dev/null || echo "0")

IDENTITY_RESP=$(api_call GET "/identities")
IDENTITY_ID=$(echo "$IDENTITY_RESP" | sed '$d' | python3 -c "import sys,json; d=json.load(sys.stdin); items=d.get('items',[]); print(items[0]['id'] if items else 0)" 2>/dev/null || echo "0")

# Run all endpoint checks
check_response "$(api_call GET "/portal-auth/me")"                              "200"     "/portal-auth/me"                       "GET"
check_response "$(timeout 10 curl -s -w "\n%{http_code}" "$API_BASE/auth/guest-enabled" 2>/dev/null)" "200" "/auth/guest-enabled" "GET"
check_response "$(api_call GET "/tenants")"                                     "200"     "/tenants"                              "GET"
check_response "$(api_call GET "/tenants/1")"                                   "200"     "/tenants/1"                            "GET"
check_response "$(api_call GET "/tenants/1/stats")"                             "200"     "/tenants/1/stats"                      "GET"
check_response "$(api_call GET "/tenants/1/users")"                             "200"     "/tenants/1/users"                      "GET"
check_response "$(api_call GET "/organizations")"                               "200"     "/organizations"                        "GET"
check_response "$(api_call GET "/organizations/$ORG_ID")"                       "200,404" "/organizations/$ORG_ID"                "GET"
check_response "$(api_call GET "/organizations/$ORG_ID/graph")"                 "200,404" "/organizations/$ORG_ID/graph"          "GET"
check_response "$(api_call GET "/entities")"                                    "200"     "/entities"                             "GET"
check_response "$(api_call GET "/entities?page=1&per_page=10")"                 "200"     "/entities?page=1&per_page=10"          "GET"
[[ "$ENTITY_ID" != "0" ]] && check_response "$(api_call GET "/entities/$ENTITY_ID")"             "200,404" "/entities/$ENTITY_ID"             "GET"
[[ "$ENTITY_ID" != "0" ]] && check_response "$(api_call GET "/entities/$ENTITY_ID/dependencies")" "200,404" "/entities/$ENTITY_ID/dependencies" "GET"
check_response "$(api_call GET "/entity-types/")"                               "200"     "/entity-types/"                        "GET"
check_response "$(api_call GET "/identities")"                                  "200"     "/identities"                           "GET"
[[ "$IDENTITY_ID" != "0" ]] && check_response "$(api_call GET "/identities/$IDENTITY_ID")" "200,404" "/identities/$IDENTITY_ID" "GET"
check_response "$(api_call GET "/identities/groups")"                           "200"     "/identities/groups"                    "GET"
check_response "$(api_call GET "/dependencies")"                                "200"     "/dependencies"                         "GET"
check_response "$(api_call GET "/graph")"                                       "200"     "/graph"                                "GET"
check_response "$(api_call GET "/graph/analyze")"                               "200"     "/graph/analyze"                        "GET"
check_response "$(api_call GET "/users")"                                       "200"     "/users"                                "GET"
check_response "$(api_call GET "/resource-roles")"                              "200"     "/resource-roles"                       "GET"
check_response "$(api_call GET "/labels")"                                      "200"     "/labels"                               "GET"
check_response "$(api_call GET "/issues")"                                      "200"     "/issues"                               "GET"
check_response "$(api_call GET "/issues/labels")"                               "200"     "/issues/labels"                        "GET"
check_response "$(api_call GET "/projects")"                                    "200"     "/projects"                             "GET"
check_response "$(api_call GET "/milestones")"                                  "200"     "/milestones"                           "GET"
[[ "$ENTITY_ID" != "0" ]] && check_response "$(api_call GET "/metadata/entities/$ENTITY_ID/metadata")" "200,404" "/metadata/entities/$ENTITY_ID/metadata" "GET"
check_response "$(api_call GET "/ipam/prefixes")"                               "200"     "/ipam/prefixes"                        "GET"
check_response "$(api_call GET "/ipam/addresses")"                              "200"     "/ipam/addresses"                       "GET"
check_response "$(api_call GET "/ipam/vlans")"                                  "200"     "/ipam/vlans"                           "GET"
check_response "$(api_call GET "/networking/networks")"                         "200"     "/networking/networks"                  "GET"
check_response "$(api_call GET "/networking/topology/connections")"             "200"     "/networking/topology/connections"      "GET"
check_response "$(api_call GET "/networking/mappings")"                         "200"     "/networking/mappings"                  "GET"
check_response "$(api_call GET "/software")"                                    "200"     "/software"                             "GET"
check_response "$(api_call GET "/services")"                                    "200"     "/services"                             "GET"
check_response "$(api_call GET "/data-stores")"                                 "200"     "/data-stores"                          "GET"
check_response "$(api_call GET "/certificates")"                                "200"     "/certificates"                         "GET"
check_response "$(api_call GET "/secrets")"                                     "200"     "/secrets"                              "GET"
check_response "$(api_call GET "/secrets/providers")"                           "200"     "/secrets/providers"                    "GET"
check_response "$(api_call GET "/keys")"                                        "200"     "/keys"                                 "GET"
check_response "$(api_call GET "/keys/providers")"                              "200"     "/keys/providers"                       "GET"
check_response "$(api_call GET "/iam/providers")"                               "200"     "/iam/providers"                        "GET"
check_response "$(api_call GET "/google-workspace/providers")"                  "200"     "/google-workspace/providers"           "GET"
check_response "$(api_call GET "/discovery/jobs")"                              "200"     "/discovery/jobs"                       "GET"
check_response "$(api_call GET "/discovery/history")"                           "200"     "/discovery/history"                    "GET"
check_response "$(api_call GET "/discovery/jobs/pending")"                      "200"     "/discovery/jobs/pending"               "GET"
check_response "$(api_call GET "/webhooks")"                                    "200"     "/webhooks"                             "GET"
check_response "$(api_call GET "/webhooks/notification-rules")"                 "200"     "/webhooks/notification-rules"          "GET"
check_response "$(api_call GET "/backup/jobs")"                                 "200"     "/backup/jobs"                          "GET"
check_response "$(api_call GET "/backup")"                                      "200"     "/backup"                               "GET"
check_response "$(api_call GET "/backup/stats")"                                "200"     "/backup/stats"                         "GET"
check_response "$(api_call GET "/search/entities?q=test")"                      "200"     "/search/entities?q=test"               "GET"
check_response "$(api_call GET "/search/organizations?q=test")"                 "200"     "/search/organizations?q=test"          "GET"
check_response "$(api_call GET "/search/suggest?q=test")"                       "200"     "/search/suggest?q=test"                "GET"
check_response "$(api_call GET "/audit/retention-policies")"                    "200"     "/audit/retention-policies"             "GET"
check_response "$(api_call GET "/audit-enterprise/logs")"                       "200"     "/audit-enterprise/logs"                "GET"
check_response "$(api_call GET "/sso/idp")"                                     "200"     "/sso/idp"                              "GET"
check_response "$(api_call GET "/api-keys")"                                    "200"     "/api-keys"                             "GET"
check_response "$(api_call GET "/on-call/rotations")"                           "200"     "/on-call/rotations"                    "GET"
check_response "$(timeout 10 curl -s -w "\n%{http_code}" "$BASE_URL/healthz" 2>/dev/null)" "200" "/healthz" "GET"

TOTAL_API=$((PASSED + FAILED))
if [[ $FAILED -gt 0 ]]; then
    log_error "API suite: $FAILED/$TOTAL_API failed"
    for ep in "${FAILED_ENDPOINTS[@]}"; do echo "    $ep"; done
    PHASE_RESULTS+=("API Suite: FAIL ($FAILED/$TOTAL_API failed)")
    API_SUITE_FAILED=true
else
    log_success "API suite: $PASSED/$TOTAL_API passed"
    PHASE_RESULTS+=("API Suite: PASS ($PASSED/$TOTAL_API)")
    API_SUITE_FAILED=false
fi

###############################################################################
# PHASE 5: Playwright browser tests
###############################################################################
log_section "Phase 5: Playwright Browser Tests"

WEB_DIR="$PROJECT_ROOT/web"
if [ ! -f "$WEB_DIR/playwright.config.ts" ]; then
    log_warn "No playwright.config.ts found — skipping browser tests"
    PHASE_RESULTS+=("Playwright: SKIPPED (no config)")
else
    cd "$WEB_DIR"
    PLAYWRIGHT_EXIT=0
    PLAYWRIGHT_BASE_URL="$BASE_URL" npx playwright test --reporter=list 2>&1 || PLAYWRIGHT_EXIT=$?
    cd "$PROJECT_ROOT"

    if [ $PLAYWRIGHT_EXIT -eq 0 ]; then
        log_success "Playwright tests passed"
        PHASE_RESULTS+=("Playwright: PASS")
    else
        log_error "Playwright tests failed (exit $PLAYWRIGHT_EXIT)"
        PHASE_RESULTS+=("Playwright: FAIL")
    fi
fi

###############################################################################
# Summary
###############################################################################
echo ""
echo -e "${BLUE}================================================${NC}"
echo -e "${BLUE}  E2E Summary${NC}"
echo -e "${BLUE}================================================${NC}"
for result in "${PHASE_RESULTS[@]}"; do
    if [[ "$result" == *"PASS"* ]]; then
        echo -e "  ${GREEN}✓${NC} $result"
    elif [[ "$result" == *"SKIP"* ]]; then
        echo -e "  ${YELLOW}○${NC} $result"
    else
        echo -e "  ${RED}✗${NC} $result"
    fi
done
echo ""

# Exit non-zero if any phase failed
for result in "${PHASE_RESULTS[@]}"; do
    [[ "$result" == *"FAIL"* ]] && exit 1
done
exit 0
