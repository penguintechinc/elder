#!/bin/bash
# Comprehensive Elder API Test Script
# Tests all API endpoints against local dev cluster

BASE_URL="http://localhost:4000/api/v1"
TENANT_ID=1
EMAIL="admin@localhost.local"
PASSWORD="admin123"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Counters
PASSED=0
FAILED=0
SKIPPED=0

# Test results array
declare -a FAILED_ENDPOINTS

log_test() {
    local status=$1
    local endpoint=$2
    local method=$3
    local details=$4

    if [[ "$status" == "PASS" ]]; then
        echo -e "${GREEN}✓ PASS${NC} [$method] $endpoint"
        ((PASSED++))
    elif [[ "$status" == "FAIL" ]]; then
        echo -e "${RED}✗ FAIL${NC} [$method] $endpoint - $details"
        ((FAILED++))
        FAILED_ENDPOINTS+=("[$method] $endpoint - $details")
    else
        echo -e "${YELLOW}○ SKIP${NC} [$method] $endpoint - $details"
        ((SKIPPED++))
    fi
}

section() {
    echo ""
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

# Function to make authenticated request with timeout
api_call() {
    local method=$1
    local endpoint=$2
    local data=$3

    if [[ -n "$data" ]]; then
        timeout 10 curl -s -w "\n%{http_code}" -X "$method" "$BASE_URL$endpoint" \
            -H "Authorization: Bearer $TOKEN" \
            -H "Content-Type: application/json" \
            -d "$data" 2>/dev/null
    else
        timeout 10 curl -s -w "\n%{http_code}" -X "$method" "$BASE_URL$endpoint" \
            -H "Authorization: Bearer $TOKEN" \
            -H "Content-Type: application/json" 2>/dev/null
    fi
}

# Parse response and check status
check_response() {
    local response=$1
    local expected_codes=$2  # comma-separated list of acceptable codes
    local endpoint=$3
    local method=$4

    if [[ -z "$response" ]]; then
        log_test "FAIL" "$endpoint" "$method" "Request timed out or no response"
        return 1
    fi

    # Extract HTTP code from last line
    local http_code
    http_code=$(echo "$response" | tail -n1)
    local body
    body=$(echo "$response" | sed '$d')

    # Check if code is in expected codes
    local found=0
    IFS=',' read -ra CODES <<< "$expected_codes"
    for code in "${CODES[@]}"; do
        if [[ "$http_code" == "$code" ]]; then
            found=1
            break
        fi
    done

    if [[ $found -eq 1 ]]; then
        log_test "PASS" "$endpoint" "$method" "HTTP $http_code"
        return 0
    else
        # Check for specific error messages
        local error
        error=$(echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('error','') or d.get('message',''))" 2>/dev/null || echo "$body" | head -c 100)
        log_test "FAIL" "$endpoint" "$method" "HTTP $http_code - $error"
        return 1
    fi
}

###########################################
# AUTHENTICATION
###########################################
section "AUTHENTICATION"

echo "Logging in to get access token..."
LOGIN_RESPONSE=$(timeout 10 curl -s -X POST "$BASE_URL/portal-auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"tenant_id\": $TENANT_ID, \"email\": \"$EMAIL\", \"password\": \"$PASSWORD\"}" 2>/dev/null)

TOKEN=$(echo "$LOGIN_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null)

if [[ -z "$TOKEN" ]]; then
    echo -e "${RED}Failed to get access token. Cannot proceed with tests.${NC}"
    echo "$LOGIN_RESPONSE"
    exit 1
fi

echo -e "${GREEN}✓ Successfully authenticated${NC}"
echo ""

###########################################
# AUTH ENDPOINTS
###########################################
section "AUTH & PROFILE ENDPOINTS"

# Portal Auth - /me
RESP=$(api_call GET "/portal-auth/me")
check_response "$RESP" "200" "/portal-auth/me" "GET"

# Guest enabled check (no auth needed)
RESP=$(timeout 10 curl -s -w "\n%{http_code}" "$BASE_URL/auth/guest-enabled" 2>/dev/null)
check_response "$RESP" "200" "/auth/guest-enabled" "GET"

###########################################
# TENANT MANAGEMENT
###########################################
section "TENANT MANAGEMENT"

RESP=$(api_call GET "/tenants")
check_response "$RESP" "200" "/tenants" "GET"

RESP=$(api_call GET "/tenants/1")
check_response "$RESP" "200" "/tenants/1" "GET"

RESP=$(api_call GET "/tenants/1/stats")
check_response "$RESP" "200" "/tenants/1/stats" "GET"

RESP=$(api_call GET "/tenants/1/users")
check_response "$RESP" "200" "/tenants/1/users" "GET"

###########################################
# ORGANIZATIONS
###########################################
section "ORGANIZATIONS"

RESP=$(api_call GET "/organizations")
check_response "$RESP" "200" "/organizations" "GET"

# Get first org ID for further tests
ORG_RESP=$(api_call GET "/organizations")
ORG_BODY=$(echo "$ORG_RESP" | sed '$d')
ORG_ID=$(echo "$ORG_BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('items',d.get('data',[]))[0].get('id',1) if d.get('items',d.get('data',[])) else 1)" 2>/dev/null || echo "1")

RESP=$(api_call GET "/organizations/$ORG_ID")
check_response "$RESP" "200,404" "/organizations/$ORG_ID" "GET"

RESP=$(api_call GET "/organizations/$ORG_ID/graph")
check_response "$RESP" "200,404" "/organizations/$ORG_ID/graph" "GET"

###########################################
# ENTITIES
###########################################
section "ENTITIES"

RESP=$(api_call GET "/entities")
check_response "$RESP" "200" "/entities" "GET"

RESP=$(api_call GET "/entities?page=1&per_page=10")
check_response "$RESP" "200" "/entities?page=1&per_page=10" "GET"

# Get first entity for tests
ENTITY_RESP=$(api_call GET "/entities")
ENTITY_BODY=$(echo "$ENTITY_RESP" | sed '$d')
ENTITY_ID=$(echo "$ENTITY_BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); items=d.get('items',d.get('data',[])); print(items[0].get('id',1) if items else 0)" 2>/dev/null || echo "0")

if [[ "$ENTITY_ID" != "0" ]]; then
    RESP=$(api_call GET "/entities/$ENTITY_ID")
    check_response "$RESP" "200,404" "/entities/$ENTITY_ID" "GET"

    RESP=$(api_call GET "/entities/$ENTITY_ID/dependencies")
    check_response "$RESP" "200,404" "/entities/$ENTITY_ID/dependencies" "GET"
fi

###########################################
# ENTITY TYPES
###########################################
section "ENTITY TYPES"

RESP=$(api_call GET "/entity-types/")
check_response "$RESP" "200" "/entity-types/" "GET"

RESP=$(api_call GET "/entity-types/server")
check_response "$RESP" "200,404" "/entity-types/server" "GET"

RESP=$(api_call GET "/entity-types/server/subtypes")
check_response "$RESP" "200,404" "/entity-types/server/subtypes" "GET"

###########################################
# IDENTITIES
###########################################
section "IDENTITIES"

RESP=$(api_call GET "/identities")
check_response "$RESP" "200" "/identities" "GET"

IDENTITY_RESP=$(api_call GET "/identities")
IDENTITY_BODY=$(echo "$IDENTITY_RESP" | sed '$d')
IDENTITY_ID=$(echo "$IDENTITY_BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); items=d.get('items',d.get('data',[])); print(items[0].get('id',1) if items else 0)" 2>/dev/null || echo "0")

if [[ "$IDENTITY_ID" != "0" ]]; then
    RESP=$(api_call GET "/identities/$IDENTITY_ID")
    check_response "$RESP" "200,404" "/identities/$IDENTITY_ID" "GET"
fi

RESP=$(api_call GET "/identities/groups")
check_response "$RESP" "200" "/identities/groups" "GET"

###########################################
# DEPENDENCIES
###########################################
section "DEPENDENCIES"

RESP=$(api_call GET "/dependencies")
check_response "$RESP" "200" "/dependencies" "GET"

###########################################
# GRAPH
###########################################
section "GRAPH"

RESP=$(api_call GET "/graph")
check_response "$RESP" "200" "/graph" "GET"

RESP=$(api_call GET "/graph/analyze")
check_response "$RESP" "200" "/graph/analyze" "GET"

###########################################
# USERS & RBAC
###########################################
section "USERS & RBAC"

RESP=$(api_call GET "/users")
check_response "$RESP" "200" "/users" "GET"

RESP=$(api_call GET "/resource-roles")
check_response "$RESP" "200" "/resource-roles" "GET"

###########################################
# LABELS
###########################################
section "LABELS"

RESP=$(api_call GET "/labels")
check_response "$RESP" "200" "/labels" "GET"

###########################################
# ISSUES
###########################################
section "ISSUES"

RESP=$(api_call GET "/issues")
check_response "$RESP" "200" "/issues" "GET"

RESP=$(api_call GET "/issues/labels")
check_response "$RESP" "200" "/issues/labels" "GET"

###########################################
# PROJECTS
###########################################
section "PROJECTS"

RESP=$(api_call GET "/projects")
check_response "$RESP" "200" "/projects" "GET"

###########################################
# MILESTONES
###########################################
section "MILESTONES"

RESP=$(api_call GET "/milestones")
check_response "$RESP" "200" "/milestones" "GET"

###########################################
# METADATA
###########################################
section "METADATA"

if [[ "$ENTITY_ID" != "0" ]]; then
    RESP=$(api_call GET "/metadata/entities/$ENTITY_ID/metadata")
    check_response "$RESP" "200,404" "/metadata/entities/$ENTITY_ID/metadata" "GET"
fi

if [[ "$ORG_ID" != "0" && "$ORG_ID" != "" ]]; then
    RESP=$(api_call GET "/metadata/organizations/$ORG_ID/metadata")
    check_response "$RESP" "200,404" "/metadata/organizations/$ORG_ID/metadata" "GET"
fi

###########################################
# IPAM
###########################################
section "IPAM"

RESP=$(api_call GET "/ipam/prefixes")
check_response "$RESP" "200" "/ipam/prefixes" "GET"

RESP=$(api_call GET "/ipam/addresses")
check_response "$RESP" "200" "/ipam/addresses" "GET"

RESP=$(api_call GET "/ipam/vlans")
check_response "$RESP" "200" "/ipam/vlans" "GET"

###########################################
# NETWORKING
###########################################
section "NETWORKING"

RESP=$(api_call GET "/networking/networks")
check_response "$RESP" "200" "/networking/networks" "GET"

RESP=$(api_call GET "/networking/topology/connections")
check_response "$RESP" "200" "/networking/topology/connections" "GET"

RESP=$(api_call GET "/networking/mappings")
check_response "$RESP" "200" "/networking/mappings" "GET"

RESP=$(api_call GET "/networking/topology/graph?organization_id=1")
check_response "$RESP" "200" "/networking/topology/graph?organization_id=1" "GET"

###########################################
# SOFTWARE
###########################################
section "SOFTWARE"

RESP=$(api_call GET "/software")
check_response "$RESP" "200" "/software" "GET"

###########################################
# SERVICES
###########################################
section "SERVICES"

RESP=$(api_call GET "/services")
check_response "$RESP" "200" "/services" "GET"

###########################################
# DATA STORES
###########################################
section "DATA STORES"

RESP=$(api_call GET "/data-stores")
check_response "$RESP" "200" "/data-stores" "GET"

###########################################
# CERTIFICATES
###########################################
section "CERTIFICATES"

RESP=$(api_call GET "/certificates")
check_response "$RESP" "200" "/certificates" "GET"

###########################################
# SECRETS
###########################################
section "SECRETS"

RESP=$(api_call GET "/secrets")
check_response "$RESP" "200" "/secrets" "GET"

RESP=$(api_call GET "/secrets/providers")
check_response "$RESP" "200" "/secrets/providers" "GET"

RESP=$(api_call GET "/builtin-secrets?organization_id=1")
check_response "$RESP" "200" "/builtin-secrets?organization_id=1" "GET"

###########################################
# KEYS
###########################################
section "KEYS"

RESP=$(api_call GET "/keys")
check_response "$RESP" "200" "/keys" "GET"

RESP=$(api_call GET "/keys/providers")
check_response "$RESP" "200" "/keys/providers" "GET"

###########################################
# IAM
###########################################
section "IAM"

RESP=$(api_call GET "/iam/providers")
check_response "$RESP" "200" "/iam/providers" "GET"

###########################################
# GOOGLE WORKSPACE
###########################################
section "GOOGLE WORKSPACE"

RESP=$(api_call GET "/google-workspace/providers")
check_response "$RESP" "200" "/google-workspace/providers" "GET"

###########################################
# DISCOVERY
###########################################
section "DISCOVERY"

RESP=$(api_call GET "/discovery/jobs")
check_response "$RESP" "200" "/discovery/jobs" "GET"

RESP=$(api_call GET "/discovery/history")
check_response "$RESP" "200" "/discovery/history" "GET"

RESP=$(api_call GET "/discovery/jobs/pending")
check_response "$RESP" "200" "/discovery/jobs/pending" "GET"

###########################################
# WEBHOOKS
###########################################
section "WEBHOOKS"

RESP=$(api_call GET "/webhooks")
check_response "$RESP" "200" "/webhooks" "GET"

RESP=$(api_call GET "/webhooks/notification-rules")
check_response "$RESP" "200" "/webhooks/notification-rules" "GET"

###########################################
# BACKUP
###########################################
section "BACKUP"

RESP=$(api_call GET "/backup/jobs")
check_response "$RESP" "200" "/backup/jobs" "GET"

RESP=$(api_call GET "/backup")
check_response "$RESP" "200" "/backup" "GET"

RESP=$(api_call GET "/backup/stats")
check_response "$RESP" "200" "/backup/stats" "GET"

###########################################
# SEARCH
###########################################
section "SEARCH"

RESP=$(api_call GET "/search")
check_response "$RESP" "200" "/search" "GET"

RESP=$(api_call GET "/search/entities?q=test")
check_response "$RESP" "200" "/search/entities?q=test" "GET"

RESP=$(api_call GET "/search/organizations?q=test")
check_response "$RESP" "200" "/search/organizations?q=test" "GET"

RESP=$(api_call GET "/search/suggest?q=test")
check_response "$RESP" "200" "/search/suggest?q=test" "GET"

# Note: /search/saved requires Identity auth (username/password), not Portal auth
# Testing with portal auth returns 401 as expected
RESP=$(api_call GET "/search/saved")
check_response "$RESP" "200,401" "/search/saved" "GET"

###########################################
# AUDIT
###########################################
section "AUDIT"

RESP=$(api_call GET "/audit/retention-policies")
check_response "$RESP" "200" "/audit/retention-policies" "GET"

RESP=$(api_call GET "/audit-enterprise/logs")
check_response "$RESP" "200" "/audit-enterprise/logs" "GET"

RESP=$(api_call GET "/audit-enterprise/retention")
check_response "$RESP" "200" "/audit-enterprise/retention" "GET"

###########################################
# SSO
###########################################
section "SSO"

RESP=$(api_call GET "/sso/idp")
check_response "$RESP" "200" "/sso/idp" "GET"

RESP=$(api_call GET "/sso/saml/metadata")
check_response "$RESP" "200" "/sso/saml/metadata" "GET"

###########################################
# API KEYS
###########################################
section "API KEYS"

RESP=$(api_call GET "/api-keys")
check_response "$RESP" "200" "/api-keys" "GET"

###########################################
# PUBLIC LOOKUP (no auth)
###########################################
section "PUBLIC LOOKUP ENDPOINTS"

RESP=$(timeout 10 curl -s -w "\n%{http_code}" "http://localhost:4000/lookup/1" 2>/dev/null)
check_response "$RESP" "200,404" "/lookup/1 (no auth)" "GET"

###########################################
# HEALTH CHECK
###########################################
section "HEALTH CHECK"

RESP=$(timeout 10 curl -s -w "\n%{http_code}" "http://localhost:4000/healthz" 2>/dev/null)
check_response "$RESP" "200" "/healthz" "GET"

###########################################
# SUMMARY
###########################################
echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  TEST SUMMARY${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  ${GREEN}PASSED:${NC}  $PASSED"
echo -e "  ${RED}FAILED:${NC}  $FAILED"
echo -e "  ${YELLOW}SKIPPED:${NC} $SKIPPED"
echo ""
TOTAL=$((PASSED + FAILED + SKIPPED))
echo -e "  TOTAL:   $TOTAL"
echo ""

if [[ $FAILED -gt 0 ]]; then
    echo -e "${RED}Some tests failed!${NC}"
    echo ""
    echo "Failed endpoints:"
    for endpoint in "${FAILED_ENDPOINTS[@]}"; do
        echo "  - $endpoint"
    done
    exit 1
else
    echo -e "${GREEN}All tests passed!${NC}"
fi
