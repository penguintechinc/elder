#!/bin/bash
# Elder API Testing Script - Run inside Docker network
# This script runs a test container inside the elder-network to test API endpoints

set -euo pipefail

echo "========================================="
echo "Elder API Testing Script (Docker Network)"
echo "========================================="
echo ""

# Run tests inside the docker network using alpine with curl and jq
docker run --rm --network elder_elder-network \
  alpine/curl:latest \
  sh -c '
    # Install jq for JSON parsing
    apk add --no-cache jq bash

    API_URL="http://api:5000"
    ADMIN_USER="admin@localhost.local"
    ADMIN_PASS="admin123"

    # Colors
    GREEN="\033[0;32m"
    RED="\033[0;31m"
    YELLOW="\033[1;33m"
    NC="\033[0m"

    echo -e "${YELLOW}Test 1: Health Check${NC}"
    response=$(curl -s "$API_URL/healthz")
    if echo "$response" | jq -e ".status == \"healthy\"" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Health check passed${NC}"
        echo "$response" | jq .
    else
        echo -e "${RED}✗ Health check failed${NC}"
        exit 1
    fi
    echo ""

    echo -e "${YELLOW}Test 2: Admin Login${NC}"
    login_response=$(curl -s -X POST "$API_URL/api/v1/auth/login" \
      -H "Content-Type: application/json" \
      -d "{\"username\":\"$ADMIN_USER\",\"password\":\"$ADMIN_PASS\"}")

    if echo "$login_response" | jq -e ".access_token" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Login successful${NC}"
        ACCESS_TOKEN=$(echo "$login_response" | jq -r ".access_token")
        echo "Access token: ${ACCESS_TOKEN:0:50}..."
    else
        echo -e "${RED}✗ Login failed${NC}"
        echo "$login_response" | jq .
        exit 1
    fi
    echo ""

    echo -e "${YELLOW}Test 3: Get Current User${NC}"
    me_response=$(curl -s "$API_URL/api/v1/auth/me" \
      -H "Authorization: Bearer $ACCESS_TOKEN")

    if echo "$me_response" | jq -e ".username == \"admin@localhost.local\" or .email == \"admin@localhost.local\"" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Current user retrieved${NC}"
        echo "$me_response" | jq "{username, email, is_superuser, is_active}"
    else
        echo -e "${RED}✗ Failed to get current user${NC}"
        echo "$me_response" | jq .
    fi
    echo ""

    echo -e "${YELLOW}Test 4: List Organizations${NC}"
    orgs_response=$(curl -s "$API_URL/api/v1/organizations" \
      -H "Authorization: Bearer $ACCESS_TOKEN")

    if echo "$orgs_response" | jq -e ".items" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Organizations listed${NC}"
        org_count=$(echo "$orgs_response" | jq ".total")
        echo "Total organizations: $org_count"
        if [ "$org_count" -gt 0 ]; then
            echo "$orgs_response" | jq ".items[0] | {id, name, description}"
        fi
    else
        echo -e "${RED}✗ Failed to list organizations${NC}"
        echo "$orgs_response" | jq .
    fi
    echo ""

    echo -e "${YELLOW}Test 5: Create Organization${NC}"
    create_org_response=$(curl -s -X POST "$API_URL/api/v1/organizations" \
      -H "Authorization: Bearer $ACCESS_TOKEN" \
      -H "Content-Type: application/json" \
      -d "{
        \"name\": \"Test Organization\",
        \"description\": \"Created by Docker network test\",
        \"parent_id\": null,
        \"attributes\": {\"environment\": \"test\", \"region\": \"us-east-1\"}
      }")

    if echo "$create_org_response" | jq -e ".id" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Organization created${NC}"
        ORG_ID=$(echo "$create_org_response" | jq -r ".id")
        echo "Organization ID: $ORG_ID"
        echo "$create_org_response" | jq "{id, name, description, attributes}"
    else
        echo -e "${RED}✗ Failed to create organization${NC}"
        echo "$create_org_response" | jq .
        ORG_ID=""
    fi
    echo ""

    echo -e "${YELLOW}Test 6: Create Entity${NC}"
    if [ ! -z "$ORG_ID" ]; then
        create_entity_response=$(curl -s -X POST "$API_URL/api/v1/entities" \
          -H "Authorization: Bearer $ACCESS_TOKEN" \
          -H "Content-Type: application/json" \
          -d "{
            \"name\": \"Web Server 01\",
            \"description\": \"Primary web server\",
            \"entity_type\": \"compute\",
            \"organization_id\": $ORG_ID,
            \"attributes\": {
              \"hostname\": \"web-01.example.com\",
              \"ip\": \"10.0.1.5\",
              \"os\": \"Ubuntu 22.04\",
              \"cpu_cores\": 4,
              \"memory_gb\": 16
            }
          }")

        if echo "$create_entity_response" | jq -e ".id" > /dev/null 2>&1; then
            echo -e "${GREEN}✓ Entity created${NC}"
            ENTITY_ID=$(echo "$create_entity_response" | jq -r ".id")
            echo "Entity ID: $ENTITY_ID"
            echo "$create_entity_response" | jq "{id, name, entity_type, organization_id, attributes}"
        else
            echo -e "${RED}✗ Failed to create entity${NC}"
            echo "$create_entity_response" | jq .
            ENTITY_ID=""
        fi
    else
        echo "Skipping entity creation (no organization)"
        ENTITY_ID=""
    fi
    echo ""

    echo -e "${YELLOW}Test 7: Create Dependency${NC}"
    if [ ! -z "$ENTITY_ID" ]; then
        # Create a second entity to establish dependency
        create_entity2_response=$(curl -s -X POST "$API_URL/api/v1/entities" \
          -H "Authorization: Bearer $ACCESS_TOKEN" \
          -H "Content-Type: application/json" \
          -d "{
            \"name\": \"Database Server\",
            \"description\": \"PostgreSQL database\",
            \"entity_type\": \"database\",
            \"organization_id\": $ORG_ID,
            \"attributes\": {\"hostname\": \"db-01.example.com\", \"port\": 5432}
          }")

        if echo "$create_entity2_response" | jq -e ".id" > /dev/null 2>&1; then
            ENTITY2_ID=$(echo "$create_entity2_response" | jq -r ".id")

            # Create dependency: Web server depends on database
            create_dep_response=$(curl -s -X POST "$API_URL/api/v1/dependencies" \
              -H "Authorization: Bearer $ACCESS_TOKEN" \
              -H "Content-Type: application/json" \
              -d "{
                \"source_entity_id\": $ENTITY_ID,
                \"target_entity_id\": $ENTITY2_ID,
                \"dependency_type\": \"runtime\",
                \"description\": \"Web server requires database\"
              }")

            if echo "$create_dep_response" | jq -e ".id" > /dev/null 2>&1; then
                echo -e "${GREEN}✓ Dependency created${NC}"
                echo "$create_dep_response" | jq "{id, source_entity_id, target_entity_id, dependency_type}"
            else
                echo -e "${RED}✗ Failed to create dependency${NC}"
                echo "$create_dep_response" | jq .
            fi
        fi
    else
        echo "Skipping dependency creation (no entities)"
    fi
    echo ""

    echo -e "${YELLOW}Test 8: Public Lookup${NC}"
    if [ ! -z "$ENTITY_ID" ]; then
        lookup_response=$(curl -s "$API_URL/lookup/$ENTITY_ID")
        if echo "$lookup_response" | jq -e ".id" > /dev/null 2>&1; then
            echo -e "${GREEN}✓ Public lookup successful${NC}"
            echo "$lookup_response" | jq "{id, name, entity_type, organization_id}"
        else
            echo -e "${RED}✗ Public lookup failed${NC}"
            echo "$lookup_response" | jq .
        fi
    else
        echo "Skipping public lookup (no entity)"
    fi
    echo ""

    echo -e "${YELLOW}Test 9: Graph Analysis${NC}"
    if [ ! -z "$ORG_ID" ]; then
        graph_response=$(curl -s "$API_URL/api/v1/graph/analyze?organization_id=$ORG_ID" \
          -H "Authorization: Bearer $ACCESS_TOKEN")

        if echo "$graph_response" | jq -e ".basic_stats" > /dev/null 2>&1; then
            echo -e "${GREEN}✓ Graph analysis successful${NC}"
            echo "$graph_response" | jq "{basic_stats, graph_metrics}"
        else
            echo -e "${RED}✗ Graph analysis failed${NC}"
            echo "$graph_response" | jq .
        fi
    else
        echo "Skipping graph analysis (no organization)"
    fi
    echo ""

    echo "========================================="
    echo -e "${GREEN}Testing Complete!${NC}"
    echo "========================================="
    echo ""
    echo "Summary:"
    echo "- API accessed via Docker network: api:5000"
    echo "- Admin credentials: $ADMIN_USER / $ADMIN_PASS"
    echo "- All services communicating within elder-network"
    if [ ! -z "$ORG_ID" ]; then
        echo "- Test organization: ID $ORG_ID"
    fi
    if [ ! -z "$ENTITY_ID" ]; then
        echo "- Test entities created: $ENTITY_ID, $ENTITY2_ID"
    fi
  '
