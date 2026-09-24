#!/bin/bash
# Elder API Testing Script
# Tests core API endpoints with admin user
#
# Deliberately NOT `set -euo pipefail`: this is an ad-hoc harness that runs
# every test in sequence and prints a pass/fail per test, continuing past
# individual failures (ORG_ID/ENTITY_ID/ROTATION_ID are only conditionally
# set by earlier tests and read again, unguarded, by later ones; adding -u
# would abort on the first "previous test didn't run" state, and the bare
# `| grep | head` in the metrics test would abort under pipefail on a
# no-match, both defeating the "run everything, report everything" design).
set -e

API_URL="http://localhost:5000"
ADMIN_USER="admin@localhost.local"
ADMIN_PASS="admin123"

echo "========================================="
echo "Elder API Testing Script"
echo "========================================="
echo ""

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test 1: Health Check
echo -e "${YELLOW}Test 1: Health Check${NC}"
response=$(curl -s "$API_URL/healthz")
if echo "$response" | jq -e '.status == "healthy"' > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Health check passed${NC}"
    echo "$response" | jq .
else
    echo -e "${RED}✗ Health check failed${NC}"
    exit 1
fi
echo ""

# Test 2: Login and get token
echo -e "${YELLOW}Test 2: Admin Login${NC}"
login_response=$(curl -s -X POST "$API_URL/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"$ADMIN_USER\",\"password\":\"$ADMIN_PASS\"}")

if echo "$login_response" | jq -e '.access_token' > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Login successful${NC}"
    ACCESS_TOKEN=$(echo "$login_response" | jq -r '.access_token')
    echo "Access token: ${ACCESS_TOKEN:0:50}..."
else
    echo -e "${RED}✗ Login failed${NC}"
    echo "$login_response" | jq .
    exit 1
fi
echo ""

# Test 3: Get current user
echo -e "${YELLOW}Test 3: Get Current User${NC}"
me_response=$(curl -s "$API_URL/api/v1/auth/me" \
  -H "Authorization: Bearer $ACCESS_TOKEN")

if echo "$me_response" | jq -e '.username == "admin@localhost.local" or .email == "admin@localhost.local"' > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Current user retrieved${NC}"
    echo "$me_response" | jq '{username, email, is_superuser, is_active}'
else
    echo -e "${RED}✗ Failed to get current user${NC}"
    echo "$me_response" | jq .
fi
echo ""

# Test 4: List organizations
echo -e "${YELLOW}Test 4: List Organizations${NC}"
orgs_response=$(curl -s "$API_URL/api/v1/organizations" \
  -H "Authorization: Bearer $ACCESS_TOKEN")

if echo "$orgs_response" | jq -e '.items' > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Organizations listed${NC}"
    org_count=$(echo "$orgs_response" | jq '.total')
    echo "Total organizations: $org_count"
    if [ "$org_count" -gt 0 ]; then
        echo "$orgs_response" | jq '.items[0] | {id, name, description}'
    fi
else
    echo -e "${RED}✗ Failed to list organizations${NC}"
    echo "$orgs_response" | jq .
fi
echo ""

# Test 5: Create organization
echo -e "${YELLOW}Test 5: Create Organization${NC}"
create_org_response=$(curl -s -X POST "$API_URL/api/v1/organizations" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Test Organization",
    "description": "Created by test script",
    "parent_id": null,
    "attributes": {"environment": "test"}
  }')

if echo "$create_org_response" | jq -e '.id' > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Organization created${NC}"
    ORG_ID=$(echo "$create_org_response" | jq -r '.id')
    echo "Organization ID: $ORG_ID"
    echo "$create_org_response" | jq '{id, name, description, attributes}'
else
    echo -e "${RED}✗ Failed to create organization${NC}"
    echo "$create_org_response" | jq .
fi
echo ""

# Test 6: List entities
echo -e "${YELLOW}Test 6: List Entities${NC}"
entities_response=$(curl -s "$API_URL/api/v1/entities" \
  -H "Authorization: Bearer $ACCESS_TOKEN")

if echo "$entities_response" | jq -e '.items' > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Entities listed${NC}"
    entity_count=$(echo "$entities_response" | jq '.total')
    echo "Total entities: $entity_count"
    if [ "$entity_count" -gt 0 ]; then
        echo "$entities_response" | jq '.items[0] | {id, name, entity_type, organization_id}'
    fi
else
    echo -e "${RED}✗ Failed to list entities${NC}"
    echo "$entities_response" | jq .
fi
echo ""

# Test 7: Create entity (if org was created)
if [ ! -z "$ORG_ID" ]; then
    echo -e "${YELLOW}Test 7: Create Entity${NC}"
    create_entity_response=$(curl -s -X POST "$API_URL/api/v1/entities" \
      -H "Authorization: Bearer $ACCESS_TOKEN" \
      -H "Content-Type: application/json" \
      -d "{
        \"name\": \"Test Server\",
        \"description\": \"Test web server\",
        \"entity_type\": \"compute\",
        \"organization_id\": $ORG_ID,
        \"attributes\": {\"hostname\": \"test-server-01\", \"ip\": \"10.0.1.100\"}
      }")

    if echo "$create_entity_response" | jq -e '.id' > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Entity created${NC}"
        ENTITY_ID=$(echo "$create_entity_response" | jq -r '.id')
        echo "Entity ID: $ENTITY_ID"
        echo "$create_entity_response" | jq '{id, name, entity_type, organization_id, attributes}'
    else
        echo -e "${RED}✗ Failed to create entity${NC}"
        echo "$create_entity_response" | jq .
    fi
    echo ""
fi

# Test 8: List identities
echo -e "${YELLOW}Test 8: List Identities${NC}"
identities_response=$(curl -s "$API_URL/api/v1/identities" \
  -H "Authorization: Bearer $ACCESS_TOKEN")

if echo "$identities_response" | jq -e '.items' > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Identities listed${NC}"
    identity_count=$(echo "$identities_response" | jq '.total')
    echo "Total identities: $identity_count"
    echo "$identities_response" | jq '.items[] | {id, username, email, is_superuser}'
else
    echo -e "${RED}✗ Failed to list identities${NC}"
    echo "$identities_response" | jq .
fi
echo ""

# Test 9: Public lookup (no auth required)
if [ ! -z "$ENTITY_ID" ]; then
    echo -e "${YELLOW}Test 9: Public Entity Lookup${NC}"
    lookup_response=$(curl -s "$API_URL/lookup/$ENTITY_ID")

    if echo "$lookup_response" | jq -e '.id' > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Public lookup successful${NC}"
        echo "$lookup_response" | jq '{id, name, entity_type, organization_id}'
    else
        echo -e "${RED}✗ Public lookup failed${NC}"
        echo "$lookup_response" | jq .
    fi
    echo ""
fi

# Test 10: Metrics endpoint
echo -e "${YELLOW}Test 10: Prometheus Metrics${NC}"
metrics_response=$(curl -s "$API_URL/metrics" | head -20)
if echo "$metrics_response" | grep -q "python_info"; then
    echo -e "${GREEN}✓ Metrics endpoint working${NC}"
    echo "First 5 metrics:"
    echo "$metrics_response" | grep "^#" | head -5
else
    echo -e "${RED}✗ Metrics endpoint failed${NC}"
fi
echo ""

echo "========================================="
echo -e "${GREEN}Testing Complete!${NC}"
echo "========================================="
echo ""
echo "Summary:"
echo "- API URL: $API_URL"
echo "- Admin credentials: $ADMIN_USER / $ADMIN_PASS"
echo "- Access token obtained: ${ACCESS_TOKEN:0:30}..."
if [ ! -z "$ORG_ID" ]; then
    echo "- Test organization created: ID $ORG_ID"
fi
if [ ! -z "$ENTITY_ID" ]; then
    echo "- Test entity created: ID $ENTITY_ID"
fi
if [ ! -z "$ROTATION_ID" ]; then
    echo "- Test on-call rotation created: ID $ROTATION_ID"
fi
echo ""

# Test On-Call Rotations (Phase 11)
echo -e "${YELLOW}Test On-Call: List Rotations${NC}"
rotations_response=$(curl -s "$API_URL/api/v1/on-call" \
  -H "Authorization: Bearer $ACCESS_TOKEN")

if echo "$rotations_response" | jq -e '.items' > /dev/null 2>&1; then
    echo -e "${GREEN}✓ On-call rotations listed${NC}"
    rotation_count=$(echo "$rotations_response" | jq '.total')
    echo "Total rotations: $rotation_count"
else
    echo -e "${RED}✗ Failed to list on-call rotations${NC}"
    echo "$rotations_response" | jq .
fi
echo ""

# Test On-Call: Create rotation (requires organization)
if [ ! -z "$ORG_ID" ]; then
    echo -e "${YELLOW}Test On-Call: Create Weekly Rotation${NC}"
    create_rotation_response=$(curl -s -X POST "$API_URL/api/v1/on-call" \
      -H "Authorization: Bearer $ACCESS_TOKEN" \
      -H "Content-Type: application/json" \
      -d "{
        \"name\": \"Test Weekly Rotation\",
        \"description\": \"Created by test script\",
        \"scope_type\": \"organization\",
        \"organization_id\": $ORG_ID,
        \"schedule_type\": \"weekly\",
        \"rotation_length_days\": 7,
        \"rotation_start_date\": \"$(date +%Y-%m-%d)\",
        \"is_active\": true
      }")

    if echo "$create_rotation_response" | jq -e '.id' > /dev/null 2>&1; then
        echo -e "${GREEN}✓ On-call rotation created${NC}"
        ROTATION_ID=$(echo "$create_rotation_response" | jq -r '.id')
        echo "Rotation ID: $ROTATION_ID"
        echo "$create_rotation_response" | jq '{id, name, schedule_type, scope_type}'
    else
        echo -e "${RED}✗ Failed to create rotation${NC}"
        echo "$create_rotation_response" | jq .
    fi
    echo ""
fi

# Test On-Call: Get current on-call for organization
if [ ! -z "$ORG_ID" ]; then
    echo -e "${YELLOW}Test On-Call: Get Current On-Call for Organization${NC}"
    current_oncall_response=$(curl -s "$API_URL/api/v1/on-call/current/organization/$ORG_ID" \
      -H "Authorization: Bearer $ACCESS_TOKEN")

    # May return 404 if no rotation exists, which is expected for a new org
    if echo "$current_oncall_response" | jq -e '.identity_name' > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Current on-call retrieved${NC}"
        echo "$current_oncall_response" | jq '{identity_name, shift_start, shift_end, is_override}'
    elif echo "$current_oncall_response" | jq -e '.error' > /dev/null 2>&1; then
        echo -e "${YELLOW}⚠ No on-call rotation configured for organization (expected for new org)${NC}"
    else
        echo -e "${RED}✗ Failed to get current on-call${NC}"
        echo "$current_oncall_response" | jq .
    fi
    echo ""
fi
echo ""
