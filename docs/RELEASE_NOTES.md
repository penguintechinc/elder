# Elder Release Notes

All notable changes to the Elder project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [3.0.2] - 2026-01-06

### ✨ New Features

#### AWS Lambda Function Ingestion
- **Lambda Sync**: AWS connector now syncs Lambda functions as entities
  - Entity type: `compute` with sub-type `serverless`
  - Tags: `["aws", "lambda", "serverless", {region}]`
- **Metadata captured**:
  - Function ARN, name, runtime, handler
  - Memory (MB), timeout (seconds), code size (bytes)
  - Architectures (arm64/x86_64), package type (Zip/Image)
  - VPC configuration (if attached): VPC ID, subnets, security groups
  - Environment variable keys (values excluded for security)
  - Lambda layers (ARNs)
  - Ephemeral storage size (/tmp)
  - Execution role ARN
- **Status tracking**: Lambda state (Active, Pending, Inactive, Failed) tracked in `status_metadata`
- **Pagination support**: Handles AWS accounts with many Lambda functions

### 🐛 Bug Fixes

#### Multi-Architecture Docker Builds (ARM64/Mac Silicon)
- **Fixed**: Web UI container now builds successfully on ARM64/Apple Silicon
- **Root cause**: npm bug #4828 where `package-lock.json` generated on x64 doesn't resolve ARM64 optional dependencies
- **Solution**: Added `.dockerignore` to exclude lock file, allowing fresh dependency resolution per-architecture
- **Affected packages**: `@rollup/rollup-linux-arm64-gnu`, `@esbuild/linux-arm64`, `lightningcss-linux-arm64-gnu`

### 📦 CI/CD Improvements

- Added `scanner`, `worker`, and `envoy` containers to multi-arch build workflow
- All 5 containers now build for both `linux/amd64` and `linux/arm64`
- Security scanning enabled for all container images

---

## [3.0.1] - 2026-01-05

### 🔒 Security Fixes

This release resolves **17 Dependabot security vulnerabilities** across Go, Python, and npm ecosystems.

#### Critical Severity
- **CVE-2024-28180**: `python-jose` ECDSA P-521 key confusion vulnerability
  - Upgraded from 3.3.0 → 3.4.0
  - An attacker could craft malicious tokens that bypass signature verification
- **CVE-2024-45337**: `golang.org/x/crypto` SSH authentication bypass
  - Upgraded from 0.18.0 → 0.45.0
  - Could allow unauthorized SSH access in specific configurations

#### High Severity
- **CVE-2024-45338**: `golang.org/x/net` HTML sanitizer XSS bypass
  - Upgraded from 0.20.0 → 0.47.0
- **GHSA-xr7q-jx4m-x55m**: `qs` array limit bypass DoS vulnerability
  - Upgraded via express 4.21.2 → 4.22.1
- **CVE-2022-31129**: `jws` HMAC signature verification bypass
  - Upgraded from 3.2.2 → 3.2.3
- **CVE-2021-3807**: `js-yaml` prototype pollution (package-lock.json)
  - Upgraded to 3.14.2/4.1.1

#### Medium Severity
- **CVE-2024-7254**: `google.golang.org/protobuf` JSON unmarshaling infinite loop
  - Upgraded from 1.32.0 → 1.36.6
- **CVE-2024-49766**: `Werkzeug` Windows device name path traversal
  - Upgraded from 3.1.3 → 3.1.4
- **CVE-2024-49767**: `Flask` session signing key fallback vulnerability
  - Upgraded from 3.1.0 → 3.1.1
- **GHSA-9wx4-h78v-vm56**: `marshmallow` Schema.load DoS vulnerability
  - Upgraded from 3.23.2 → 3.26.2

#### Low Severity
- **CVE-2023-45288**: `golang.org/x/net` HTTP/2 CONTINUATION flood
- Additional transitive dependency updates for comprehensive security coverage

### ✨ New Features

#### On-Call Rotation Management (Community Feature)
- **Rotation Scheduling**: Create and manage on-call schedules
  - Daily, weekly, bi-weekly, and custom rotation patterns
  - Support for multiple participants with handoff times
  - Timezone-aware scheduling
- **Participant Management**: Full CRUD for rotation participants
  - Primary and backup on-call assignments
  - Override schedules for holidays and PTO
- **History Tracking**: Complete audit log of rotation changes
  - Track who was on-call at any point in time
  - Historical analytics for on-call burden distribution
- **Webhook Integration**: Alert integrations for on-call notifications
  - Configurable webhook endpoints for rotation changes
  - Support for Slack, PagerDuty, and custom integrations
- **Web UI Components**:
  - `OnCallRotations.tsx`: Main rotations management page
  - `CreateOnCallRotationModal.tsx`: Create new rotations
  - `OnCallRotationDetailModal.tsx`: View rotation details
  - `OnCallBadge.tsx`: Visual indicator for current on-call status
- **API Endpoints**: Full CRUD at `/api/v1/on-call-rotations`
- **Database Migration**: `008_add_on_call_rotation_tables.py`

### 📦 Dependencies Updated

#### Go (shared/go_libs)
| Package | From | To |
|---------|------|-----|
| golang.org/x/crypto | 0.18.0 | 0.45.0 |
| golang.org/x/net | 0.20.0 | 0.47.0 |
| google.golang.org/protobuf | 1.32.0 | 1.36.6 |

#### Python (requirements.txt)
| Package | From | To |
|---------|------|-----|
| python-jose[cryptography] | 3.3.0 | 3.4.0 |
| Flask[async] | 3.1.0 | 3.1.1 |
| Werkzeug | 3.1.3 | 3.1.4 |
| marshmallow | 3.23.2 | 3.26.2 |

#### npm (package.json)
| Package | From | To |
|---------|------|-----|
| express | 4.21.2 | 4.22.1 |
| jws | 3.2.2 | 3.2.3 |
| js-yaml | various | 3.14.2/4.1.1 |
| qs | 6.13.0 | 6.14.1 |

---

## [3.0.0] - 2025-11-25

### 💥 Breaking Changes

This release includes database schema changes that require migration.

#### Database Schema Updates
- **crypto_keys table**: Added new fields for enhanced key management
  - `key_provider_id` (renamed from `provider_id`)
  - `provider_key_arn` (string, 512 chars)
  - `key_type` (string, 50 chars, default: "symmetric")
  - `key_state` (string, 50 chars, default: "Enabled")
  - `metadata_json` (renamed from `metadata`)
- **key_access_log table**: Added new fields for better audit logging
  - `operation` (string, 50 chars)
  - `metadata_json` (json)

### ✨ New Features

#### Data Store Tracking (Community Feature)
- **Comprehensive Data Store Management**: Track sensitive data stores across your organization
  - Storage types: database, object_storage, file_system, data_warehouse, data_lake, cache, message_queue, other
  - Storage providers: AWS S3, Azure Blob, GCP Cloud Storage, MinIO, local, on-prem, etc.
  - Location tracking: region and physical location
  - Point of contact (POC) linked to Identity
- **Data Classification System**: Four-level classification for security and compliance
  - Public, Internal, Confidential, Restricted
  - Color-coded badges in UI (green, blue, yellow, red)
- **Sensitive Data Flags**: Track PII/PHI/PCI data presence
  - `contains_pii`: Personal Identifiable Information
  - `contains_phi`: Protected Health Information (HIPAA)
  - `contains_pci`: Payment Card Industry data (PCI-DSS)
  - Visual badges in UI for quick identification
- **Compliance Framework Tracking**: JSONB field for compliance frameworks
  - SOC2, HIPAA, GDPR, PCI-DSS, ISO27001, and custom frameworks
- **Encryption Tracking**: Monitor encryption status
  - `encryption_at_rest`: Data encrypted when stored
  - `encryption_in_transit`: Data encrypted during transfer
  - `encryption_key_id`: Link to crypto_keys table
- **Backup Configuration**: Track backup policies
  - `backup_enabled`: Whether backups are configured
  - `backup_frequency`: Backup schedule (daily, weekly, monthly)
- **Access Control**: Track access policies
  - `access_control_type`: private, internal, public
- **Additional Metadata**: Size, retention, audit tracking
  - `size_bytes`: Data store size
  - `retention_days`: Data retention policy
  - `last_access_audit`: Last audit timestamp
  - `metadata`: Custom JSONB field
- **Label Support**: Categorize data stores with labels via junction table
- **API Endpoints**: Full CRUD at `/api/v1/data-stores` (8 endpoints)
  - `GET /api/v1/data-stores` - List with filtering
  - `POST /api/v1/data-stores` - Create new data store
  - `GET /api/v1/data-stores/<id>` - Get details
  - `PUT /api/v1/data-stores/<id>` - Update
  - `DELETE /api/v1/data-stores/<id>` - Delete
  - `GET /api/v1/data-stores/<id>/labels` - Get labels
  - `POST /api/v1/data-stores/<id>/labels` - Add label
  - `DELETE /api/v1/data-stores/<id>/labels/<label_id>` - Remove label
- **Web UI**: Dedicated Data Stores page in Infrastructure section
  - Card-based display with classification badges
  - Filtering by classification, storage type, region
  - Create/Edit/Detail modals with comprehensive fields
  - PII/PHI/PCI indicator badges

#### Group Membership Management (Enterprise Feature)
- **Group Ownership**: Assign identity or group as owner
  - `owner_identity_id`: Individual owner
  - `owner_group_id`: Group owner (nested ownership)
- **Multi-Approval Workflows**: Configurable approval modes
  - `approval_mode`: any, all, threshold
  - `approval_threshold`: Number of required approvals
- **Provider Sync Support**: External identity provider integration
  - `provider`: internal, ldap, okta, authentik
  - `provider_group_id`: External group identifier
  - `sync_enabled`: Enable/disable sync
- **Access Request System**: Self-service group access requests
  - `group_access_requests` table for request tracking
  - `group_access_approvals` table for multi-approver workflows
  - Request justification and expiration support
- **Membership Expiration**: Time-limited group memberships
  - `expires_at`: Automatic membership expiration
  - `granted_via_request_id`: Link to access request
- **API Endpoints**: Full workflow at `/api/v1/group-membership`

#### Authentik Identity Provider Connector (Enterprise Feature)
- **Bidirectional Group Sync**: Full sync with Authentik IdP
  - Sync users and groups from Authentik API v3
  - Write-back support: add/remove members from groups
  - Bearer token authentication
  - Pagination support for large directories
- **Configuration Settings**: Added to connector settings
  - `authentik_enabled`, `authentik_domain`, `authentik_api_token`
  - `authentik_sync_interval`, `authentik_sync_users`, `authentik_sync_groups`
  - `authentik_write_back_enabled`, `authentik_verify_ssl`
- **Entity Mappings**:
  - Users → identity (employee/serviceAccount based on attributes)
  - Groups → identity_group with nested group support
- **File**: `apps/worker/connectors/authentik_connector.py` (474 lines)

#### OIDC SSO Support (Enterprise Feature)
- **OpenID Connect**: Full OIDC support alongside existing SAML
  - OIDC Discovery (.well-known/openid-configuration)
  - Authorization Code Flow with JWT validation using JWKS
  - Just-in-Time (JIT) user provisioning
  - Token refresh capability
  - RP-Initiated Logout support
- **Database Schema**: New OIDC fields in `idp_configurations` table
  - `oidc_client_id`, `oidc_client_secret`, `oidc_issuer_url`
  - `oidc_scopes`, `oidc_response_type`, `oidc_token_endpoint_auth_method`
- **API Endpoints**: 5 new endpoints at `/api/v1/sso/oidc/`
  - `GET /sso/oidc/authorize/<idp_id>` - Initiate OIDC login
  - `GET /sso/oidc/callback` - Handle callback with code exchange
  - `POST /sso/oidc/logout/<idp_id>` - OIDC logout
  - `GET /sso/oidc/userinfo/<idp_id>` - Get user info from IdP
  - `POST /sso/oidc/refresh/<idp_id>` - Refresh access token
- **Supported Providers**:
  - Google Workspace, Microsoft Azure AD/Entra ID, Okta, Auth0
  - Keycloak, GitLab, GitHub, and any OIDC-compliant provider
- **File**: `apps/api/services/sso/oidc_service.py` (496 lines)

### 🐛 Bug Fixes

#### API Endpoint Fixes (20 endpoints fixed)
- **Module-level database import issues**: Fixed `db = None` errors in:
  - `/api/v1/api-keys` - API keys management
  - `/api/v1/sso/idp` - SSO identity provider configuration
  - `/api/v1/audit-enterprise/*` - Enterprise audit logging endpoints
- **Empty list SQL errors**: Fixed PyDAL `.belongs([])` generating invalid SQL in:
  - `/api/v1/graph` - Dependency graph visualization
  - `/api/v1/graph/analyze` - Graph analysis
  - `/api/v1/organizations/{id}/graph` - Organization relationship graph
- **Table name mismatch**: Fixed Keys service using wrong table reference
  - Changed `self.db.keys` to `self.db.crypto_keys` throughout service

### ✨ Improvements

- **Documentation**: Added default login credentials section to README.md
- **API Testing**: Added comprehensive API test script at `scripts/test/test_api_endpoints.sh`

### 📦 Files Added

- `apps/api/api/v1/data_stores.py` (300+ lines) - Data Store API endpoints
- `apps/api/api/v1/group_membership.py` (400+ lines) - Group Membership API endpoints
- `apps/api/services/group_membership/service.py` - Group membership service
- `apps/api/services/group_membership/ldap_connector.py` - LDAP sync connector
- `apps/api/services/group_membership/okta_connector.py` - Okta sync connector
- `apps/api/services/sso/oidc_service.py` (496 lines) - OIDC SSO service
- `apps/worker/connectors/authentik_connector.py` (474 lines) - Authentik IdP connector
- `apps/api/migrations/007_v300_data_stores_group_membership.sql` - v3.0.0 migration
- `web/src/pages/DataStores.tsx` (500+ lines) - Data Stores management UI

### 📝 Files Modified

- `apps/api/api/v1/graph.py` - Empty list guards
- `apps/api/api/v1/api_keys.py` - Database import fix
- `apps/api/services/sso/saml_service.py` - Database import fix
- `apps/api/services/sso/scim_service.py` - Database import fix
- `apps/api/services/audit/service.py` - Database import fix
- `apps/api/models/pydal_models.py` - Schema updates + data_stores table
- `apps/api/services/keys/service.py` - Table reference fix
- `apps/api/main.py` - Registered data_stores and group_membership blueprints
- `web/src/components/Layout.tsx` - Added Data Stores to navigation
- `web/src/App.tsx` - Added Data Stores route
- `web/src/lib/api.ts` - Added data stores API functions
- `README.md` - Default login section

### 📊 Statistics

- **Total API Endpoints**: 97 (added 8 data-stores + group-membership + 5 OIDC endpoints)
- **Total Database Tables**: 33 (added data_stores, data_store_labels, group_access_requests, group_access_approvals)
- **Frontend Pages**: 36 (added Data Stores page)
- **Connectors**: 8 (added Authentik connector)

### ⬆️ Migration Notes

After updating to v3.0.0, run the migration file `007_v300_data_stores_group_membership.sql`:

```bash
# Run migration for v3.0.0 features
docker compose exec postgres psql -U elder -d elder -f /migrations/007_v300_data_stores_group_membership.sql
```

Or apply the following SQL changes manually:

```sql
-- Part 1: Data Stores (Community Feature)
CREATE TABLE IF NOT EXISTS data_stores (
    id SERIAL PRIMARY KEY,
    tenant_id INTEGER NOT NULL DEFAULT 1 REFERENCES tenants(id) ON DELETE CASCADE,
    organization_id INTEGER REFERENCES organizations(id) ON DELETE CASCADE,
    village_id VARCHAR(32) UNIQUE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    storage_type VARCHAR(50) DEFAULT 'other',
    storage_provider VARCHAR(100),
    location_region VARCHAR(50),
    location_physical VARCHAR(255),
    data_classification VARCHAR(20) DEFAULT 'internal',
    encryption_at_rest BOOLEAN DEFAULT FALSE,
    encryption_in_transit BOOLEAN DEFAULT FALSE,
    encryption_key_id INTEGER REFERENCES crypto_keys(id) ON DELETE SET NULL,
    retention_days INTEGER,
    backup_enabled BOOLEAN DEFAULT FALSE,
    backup_frequency VARCHAR(50),
    access_control_type VARCHAR(20) DEFAULT 'private',
    poc_identity_id INTEGER REFERENCES identities(id) ON DELETE SET NULL,
    compliance_frameworks JSONB,
    contains_pii BOOLEAN DEFAULT FALSE,
    contains_phi BOOLEAN DEFAULT FALSE,
    contains_pci BOOLEAN DEFAULT FALSE,
    size_bytes BIGINT,
    last_access_audit TIMESTAMP,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP,
    created_by INTEGER REFERENCES portal_users(id) ON DELETE SET NULL,
    is_active BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS data_store_labels (
    id SERIAL PRIMARY KEY,
    data_store_id INTEGER NOT NULL REFERENCES data_stores(id) ON DELETE CASCADE,
    label_id INTEGER NOT NULL REFERENCES issue_labels(id) ON DELETE CASCADE,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(data_store_id, label_id)
);

-- Part 2: Group Membership Management (Enterprise)
ALTER TABLE identity_groups ADD COLUMN IF NOT EXISTS owner_identity_id INTEGER REFERENCES identities(id) ON DELETE SET NULL;
ALTER TABLE identity_groups ADD COLUMN IF NOT EXISTS owner_group_id INTEGER REFERENCES identity_groups(id) ON DELETE SET NULL;
ALTER TABLE identity_groups ADD COLUMN IF NOT EXISTS approval_mode VARCHAR(20) DEFAULT 'any';
ALTER TABLE identity_groups ADD COLUMN IF NOT EXISTS approval_threshold INTEGER DEFAULT 1;
ALTER TABLE identity_groups ADD COLUMN IF NOT EXISTS sync_enabled BOOLEAN DEFAULT FALSE;
ALTER TABLE identity_groups ADD COLUMN IF NOT EXISTS provider VARCHAR(50) DEFAULT 'internal';
ALTER TABLE identity_groups ADD COLUMN IF NOT EXISTS provider_group_id VARCHAR(512);

ALTER TABLE identity_group_memberships ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP;
ALTER TABLE identity_group_memberships ADD COLUMN IF NOT EXISTS granted_via_request_id INTEGER;
ALTER TABLE identity_group_memberships ADD COLUMN IF NOT EXISTS provider_synced BOOLEAN DEFAULT FALSE;
ALTER TABLE identity_group_memberships ADD COLUMN IF NOT EXISTS provider_synced_at TIMESTAMP;
ALTER TABLE identity_group_memberships ADD COLUMN IF NOT EXISTS provider_member_id VARCHAR(512);

-- Part 3: Previous crypto_keys updates
ALTER TABLE crypto_keys RENAME COLUMN provider_id TO key_provider_id;
ALTER TABLE crypto_keys ADD COLUMN provider_key_arn VARCHAR(512);
ALTER TABLE crypto_keys ADD COLUMN key_type VARCHAR(50) DEFAULT 'symmetric';
ALTER TABLE crypto_keys ADD COLUMN key_state VARCHAR(50) DEFAULT 'Enabled';
ALTER TABLE crypto_keys RENAME COLUMN metadata TO metadata_json;

ALTER TABLE key_access_log ADD COLUMN operation VARCHAR(50);
ALTER TABLE key_access_log ADD COLUMN metadata_json JSON;
```

---

## [2.4.0] - 2025-11-24

### 🔒 Certificate Management, Code Consolidation & Bug Fixes

This release introduces comprehensive SSL/TLS certificate management, significant code consolidation using ModalFormBuilder, and critical bug fixes for village_id schema constraints.

### ✨ New Features

#### SSL/TLS Certificate Management
- **Comprehensive Certificate Tracking**: Complete system for managing SSL/TLS certificates
  - Support for 15+ certificate authorities (DigiCert, Let's Encrypt, Self-Signed, Sectigo, GoDaddy, etc.)
  - Support for 11+ certificate types (CA Root, CA Intermediate, Server Cert, Wildcard, SAN, Client Cert, Code Signing, etc.)
  - Full X.509 certificate metadata tracking
    - Subject information (CN, SAN, OU, locality, state, country)
    - Issuer information
    - Key details (algorithm, size, signature algorithm)
    - Certificate content (PEM, SHA1/SHA256 fingerprints, serial number)
  - Date management (issue date, expiration date, not_before, not_after)
  - Renewal automation (auto_renew flag, renewal_days_before threshold, renewal_method)
  - ACME/Let's Encrypt integration (account URL, order URL, challenge type)
  - Revocation tracking (is_revoked, revoked_at, revocation_reason)
  - Compliance tracking (validation type DV/OV/EV, CT log status, OCSP must-staple)
  - Cost tracking (annual cost, purchase date, vendor)
  - Usage tracking (entities_using JSONB, services_using array)
  - Storage references (file_path, vault_path, private_key_secret_id)
- **API Endpoints**: Full CRUD at `/api/v1/certificates`
  - List with filtering (organization, creator, cert type, status, expiring_soon)
  - Create, get, update, delete operations
  - Dynamic status calculation (active, expiring_soon, expired, revoked)
  - Role-based access control (viewer can create, maintainer can update/delete)
- **Web UI**: Dedicated Certificates page in Security section
  - Card-based certificate display
  - Color-coded status badges (green=active, yellow=expiring_soon, red=expired, gray=revoked)
  - Filtering by creator, cert type, and organization
  - Create/edit modals using ModalFormBuilder
  - Detail modal for viewing full certificate information
  - Support for PEM certificate content display
- **Database Schema**: Migration 006 - certificates table with 50+ fields, 16 indexes including GIN indexes for JSONB/arrays
- **Village ID Support**: Universal referencing for all certificates

### 🔧 Improvements

#### Code Consolidation (DRY Principle)
- **FormBuilder Pattern Migration** (Phase 1):
  - Migrated Keys.tsx CreateProviderModal (95 lines → 25 lines with FormConfig)
  - Migrated Secrets.tsx CreateProviderModal (115 lines → modular with dynamic help text)
  - Migrated Keys.tsx Encrypt/Decrypt modals (134 lines → hybrid approach)
  - Total lines saved: 245-285 lines
  - Improved code consistency and maintainability
  - Enhanced form validation and error handling
- **Shared Helper Modules**: Backend consolidation
  - Created `api_responses.py` for standardized JSON responses
  - Created `validation_helpers.py` for common validation patterns
  - Created `crud_helpers.py` for reusable CRUD operations
  - Created `pydal_helpers.py` for PyDAL database utilities
  - Total backend consolidation savings: 150+ lines
- **Cache Invalidation Pattern**: Frontend optimization
  - Standardized React Query cache invalidation across all pages
  - Query key management utilities
  - Consistent refetch patterns

### 🐛 Bug Fixes

- **Village ID Schema Fix**: Expanded village_id from VARCHAR(16) to VARCHAR(32)
  - Village ID format is `TTTT-OOOO-IIIIIIII` (18 chars), original schema was too small
  - Migration 005 safely expands all village_id columns across 14 tables
  - Provides extra headroom for future format changes
- **PyDAL Model Syntax**: Fixed unterminated string literals in certificates model
- **TypeScript Compilation**: Fixed API call signatures for encryptData() and decryptData() in Keys.tsx
- **Screenshot Updates**: Refreshed documentation screenshots
- **Cache Invalidation**: Improved React Query cache management across multiple pages

### 📦 Files Added

- `apps/api/api/v1/certificates.py` (512 lines) - Certificate API endpoints
- `apps/api/migrations/006_add_certificates.sql` (150 lines) - Certificates database schema
- `web/src/pages/Certificates.tsx` (640 lines) - Certificate management UI
- `apps/api/migrations/005_expand_village_id_length.sql` - Village ID schema fix

### 📝 Files Modified

- Backend: `main.py`, `pydal_models.py`, `api_responses.py`, `validation_helpers.py`, `crud_helpers.py`, `pydal_helpers.py`
- Frontend: `App.tsx`, `Layout.tsx`, `api.ts`, `Keys.tsx`, `Secrets.tsx`
- Documentation: `README.md` (version 2.4.0, updated license terms), `RELEASE_NOTES.md`

### 🔄 Database Migrations

- **Migration 005**: Expand village_id columns from VARCHAR(16) to VARCHAR(32) across all tables
- **Migration 006**: Add certificates table with comprehensive SSL/TLS certificate tracking

### 📊 Statistics

- Total API Endpoints: 84 (added 5 certificate endpoints)
- Total Database Tables: 31 (added certificates table)
- Frontend Pages: 35 (added Certificates page)
- Code Consolidation: 395-435 lines saved through DRY refactoring
- License: Updated to Limited AGPL v3 with preamble for fair use

---

## [2.3.0] - 2025-11-23

### 🚀 Resource Tracking, Village ID, FormBuilder & Infrastructure Management

Major release adding Software tracking, Services tracking, IP Address Management (IPAM), Village ID universal identifiers, polymorphic dependencies, interactive Resource Map visualization, and a comprehensive FormBuilder system with security validations.

### ✨ New Features

#### Village ID System
- **Hierarchical Resource Identifier**: Every trackable item gets a unique 64-bit hex code
  - Format: `TTTT-OOOO-IIIIIIII` (18 chars with dashes)
  - `TTTT`: 16-bit tenant segment (randomized, prevents enumeration)
  - `OOOO`: 16-bit organization segment (randomized)
  - `IIIIIIII`: 32-bit item segment (~4.3 billion items per org)
  - Tenants: `a1b2-0000-00000000`
  - Organizations: `a1b2-c3d4-00000000`
  - Items: `a1b2-c3d4-e5f67890`
- **Instant Hierarchy Visibility**: See tenant/org ownership from the ID itself
- **URL Resolution**: Access any resource via `/id/{village_id}`
  - API returns resource type, ID, and redirect URL
  - Web UI redirects to appropriate detail page
- **UI Integration**: VillageIdBadge component shows ID with copy button
- **Tables Updated**: tenants, organizations, entities, identities, software, services, ipam_prefixes, ipam_addresses, ipam_vlans, issues, projects, milestones, metadata_fields, resource_roles, dependencies
- **New Columns**: `village_segment` added to tenants and organizations for hierarchy building

#### Software Tracking
- **Track software licenses and purchases** across your organization
  - Name, description, version, vendor, support contact
  - Software type (SaaS, On-Premise, Open Source, etc.)
  - License URL and business purpose
  - Seats, monthly cost, renewal dates
  - Purchasing POC (linked to Identity)
- **API Endpoints**: Full CRUD at `/api/v1/software`
- **Web UI**: Dedicated Software page with filtering and search

#### Services Tracking
- **Track microservices and applications** in your infrastructure
  - Domains, paths, port, health endpoint
  - Language, deployment method/type
  - Repository and documentation URLs
  - SLA uptime and response time targets
  - POC Identity for service ownership
  - Public/private visibility
- **API Endpoints**: Full CRUD at `/api/v1/services`
- **Web UI**: Services page with language/deployment filters

#### IP Address Management (IPAM)
- **Hierarchical CIDR Management**:
  - Prefixes with parent-child relationships for CIDR tree
  - Individual addresses assigned to prefixes
  - VLANs with VID, name, and role
- **Prefix Features**: VRF, VLAN ID, status, role, site, region, pool flag
- **Address Features**: DNS name, status, NAT inside reference, assigned object linking
- **VLAN Features**: VID, status, role, site
- **API Endpoints**:
  - `/api/v1/ipam/prefixes` with `/tree` endpoint for hierarchical view
  - `/api/v1/ipam/addresses`
  - `/api/v1/ipam/vlans`
- **Web UI**: Three-tab IPAM page with collapsible CIDR tree view

#### Polymorphic Dependencies System
- **Universal Resource Linking**: Dependencies now connect ANY resource types to each other
  - Organizations, Entities, Identities, Projects, Milestones, Issues
  - Replaces old entity-to-entity only dependencies
  - Enables tracking relationships like "Identity manages Entity" or "Issue affects Project"
- **Database Schema**: New polymorphic structure
  - `source_type` / `source_id` - Source resource
  - `target_type` / `target_id` - Target resource
  - `dependency_type` - Relationship type (manages, depends_on, related_to, etc.)
  - `tenant_id` - Multi-tenant isolation
- **Migration**: Automatic conversion of existing entity-entity dependencies
- **API Endpoints**: Full CRUD with filtering by resource type
  - `GET /api/v1/dependencies/resource/<type>/<id>` - Get all dependencies for a resource

#### Interactive Resource Map
- **New Map Page**: Full-page network visualization at `/map`
  - Visio-style graph showing all resources and relationships
  - Hierarchical and dependency edges visualized
  - Click nodes to view details
- **Comprehensive Filters**:
  - Organization scope (global or specific org with children)
  - Resource types (Organization, Entity, Identity, Project, Milestone, Issue)
  - Entity subtypes (Network, Compute, Storage, etc.)
  - Toggle hierarchical vs dependency links
  - Node limit slider (50-1000)
- **Visual Legend**: Color-coded by resource and entity types
- **Statistics**: Node/edge counts with truncation indicator
- **API Endpoint**: `GET /api/v1/graph/map` with full filtering support

#### Docker Compose V2 Upgrade
- **Updated to Docker Compose V2** (v2.40.3)
  - Faster builds and better error handling
  - Fixes `ContainerConfig` KeyError issues from v1
- **New syntax**: `docker compose` (with space) instead of `docker-compose`
- **Installation instructions** added to CLAUDE.md

#### FormBuilder System
- **Declarative Form Definition**: Web2py-style form configuration with automatic space handling
  - 16 field types: text, email, username, password, password_generate, url, domain, ip, path, slug, textarea, select, number, checkbox, date, color, multiline
  - Automatic space processing by field type (strip all, trim only, etc.)
  - Field validation with type-specific rules
- **Security Validations**: Comprehensive injection attack prevention
  - SQL injection pattern detection
  - Script/XSS injection blocking
  - Format string attack prevention
  - Command injection protection
  - Character whitelisting per field type
- **Conditional Field Visibility**:
  - `triggerField` parameter: Show field when another field is truthy
  - `showWhen` function: Custom visibility conditions
- **password_generate Field Type**: Password field with generator button
  - Visible text (not masked) for easy copy
  - 14-character alphanumeric generation
  - Restricted special characters: `! @ # $ % ^ & * ( ) - _ = +`
- **UI Enhancements**:
  - Gold (yellow-500) labels by default
  - Red error messages below invalid fields
  - Real-time validation feedback
- **17 Forms Converted**: All user input forms migrated to FormBuilder
- **Documentation**: Complete guide at `docs/development/form-builder.md`

#### Portal User Password Management
- **must_change_password Field**: Force password change on first login
  - Checkbox in identity creation modal (for portal users only)
  - Defaults to true for new portal users
  - Stored in Identity model for API enforcement
- **Conditional Display**: Only shown when "Create as Portal User" is checked

### 🔧 Database Schema Changes

#### New Tables (5)
- `software` - Software license and purchase tracking
- `services` - Microservice and application tracking
- `ipam_prefixes` - IP address prefixes/subnets with hierarchy
- `ipam_addresses` - Individual IP address management
- `ipam_vlans` - VLAN management

#### Updated Tables (16)
- `dependencies` - Converted to polymorphic structure
  - Removed: `source_entity_id`, `target_entity_id`
  - Added: `tenant_id`, `source_type`, `source_id`, `target_type`, `target_id`
  - Migration: `002_polymorphic_dependencies.sql`
- **Village ID added to 15 tables**: `village_id VARCHAR(18) UNIQUE` (hierarchical format)
  - tenants, organizations, entities, identities, software, services
  - ipam_prefixes, ipam_addresses, ipam_vlans, issues, projects
  - milestones, metadata_fields, resource_roles, dependencies
  - `village_segment VARCHAR(4)` added to tenants and organizations
  - Migration: `004_add_village_id.sql`

#### Migrations
- `002_polymorphic_dependencies.sql` - Polymorphic dependency structure
- `003_v230_software_services_ipam.sql` - Software, Services, IPAM tables
- `004_add_village_id.sql` - Village ID column additions

### 📊 Technical Details

#### Valid Resource Types
```python
VALID_RESOURCE_TYPES = [
    "organization", "entity", "identity",
    "project", "milestone", "issue",
    "software", "service", "ipam_prefix",
    "ipam_address", "ipam_vlan"
]
```

#### Map API Parameters
| Parameter | Description |
|-----------|-------------|
| `tenant_id` | Filter by tenant |
| `organization_id` | Filter by org (includes children) |
| `resource_types` | Comma-separated list |
| `entity_types` | Comma-separated entity subtypes |
| `include_hierarchical` | Include parent-child edges |
| `include_dependencies` | Include dependency edges |
| `limit` | Max nodes (default: 500) |

### 📝 Files Added/Modified

**Backend**:
- `apps/api/api/v1/software.py` - NEW: Software tracking CRUD endpoints
- `apps/api/api/v1/services.py` - NEW: Services tracking CRUD endpoints
- `apps/api/api/v1/ipam.py` - NEW: IPAM prefixes, addresses, VLANs endpoints
- `apps/api/api/v1/lookup_village_id.py` - NEW: Village ID resolution endpoint
- `apps/api/utils/village_id.py` - NEW: Village ID generation utility
- `apps/api/api/v1/dependencies.py` - Complete rewrite for polymorphic dependencies
- `apps/api/api/v1/graph.py` - Added `/map` endpoint with `_get_node_style_by_resource()`
- `apps/api/models/pydal_models.py` - Added software, services, IPAM tables, village_id fields
- `apps/api/models/dataclasses.py` - Updated `DependencyDTO` with polymorphic fields
- `apps/api/main.py` - Registered new blueprints (software, services, ipam, lookup_village_id)
- `apps/api/migrations/002_polymorphic_dependencies.sql` - Schema migration
- `apps/api/migrations/003_v230_software_services_ipam.sql` - NEW: v2.3.0 tables
- `apps/api/migrations/004_add_village_id.sql` - NEW: Village ID columns

**Frontend**:
- `web/src/pages/Software.tsx` - NEW: Software tracking page with filters
- `web/src/pages/Services.tsx` - NEW: Services tracking page with filters
- `web/src/pages/IPAM.tsx` - NEW: Three-tab IPAM page with CIDR tree
- `web/src/pages/Map.tsx` - NEW: Full map page with filters and visualization
- `web/src/pages/Dependencies.tsx` - Updated for polymorphic dependencies
- `web/src/pages/Entities.tsx` - Added VillageIdBadge display
- `web/src/components/VillageIdBadge.tsx` - NEW: Village ID display with copy
- `web/src/components/VillageIdRedirect.tsx` - NEW: Village ID resolution redirect
- `web/src/lib/api.ts` - Added software, services, IPAM, village_id functions
- `web/src/components/Layout.tsx` - Added Software, Services, IPAM, Map to navigation
- `web/src/App.tsx` - Added routes for new pages and village_id redirect
- `web/src/types/form.ts` - NEW: FormBuilder types, validation, and processing functions
- `web/src/components/FormBuilder.tsx` - NEW: Main form renderer component
- `web/src/components/ModalFormBuilder.tsx` - NEW: Modal wrapper for FormBuilder
- `web/src/components/CreateIdentityModal.tsx` - Updated with FormBuilder and must_change_password
- `web/src/pages/IAM.tsx` - Updated with FormBuilder and must_change_password
- 17 form pages converted to use FormBuilder system

**Documentation**:
- `CLAUDE.md` - Added Elder Terminology section, Docker Compose V2 syntax
- `docs/RELEASE_NOTES.md` - Updated with v2.3.0 features
- `docs/development/form-builder.md` - NEW: FormBuilder system documentation

**Backend (Identity)**:
- `apps/api/models/identity.py` - Added must_change_password column
- `apps/api/api/v1/identities.py` - Added must_change_password to create endpoint

### 🔍 Breaking Changes

**None for end users.** Existing dependencies are automatically migrated to polymorphic format.

**For developers**:
- Dependencies API now uses `source_type`/`source_id` instead of `source_entity_id`
- Use `docker compose` (V2) instead of `docker-compose` (V1)

### 🎯 Use Cases

The polymorphic dependency system enables tracking relationships like:
- **Identity → Entity**: "John manages Server-01"
- **Issue → Entity**: "Bug #42 affects Database-Prod"
- **Project → Organization**: "Migration Project belongs to IT Dept"
- **Entity → Entity**: "App-Server depends on Load-Balancer"

### 🙏 Acknowledgments

**Development Timeline**: November 22-23, 2025
**Major Focus**: Polymorphic dependencies, resource map visualization, Docker Compose V2, FormBuilder system with security validations

---

## [2.2.0] - 2025-11-20

### 🚀 Enterprise Edition - Multi-Tenancy Foundation

Major release transforming Elder into a full Enterprise multi-tenant platform. This release lays the database foundation for 3-tier permissions, SSO/SAML/SCIM support, and comprehensive tenant isolation. Fully backwards compatible with existing v2.x deployments through the default System tenant.

**Target Use Cases:** Hosting companies, Managed Service Providers (MSPs), Companies with subsidiaries

### ✨ New Features

#### Multi-Tenancy Database Foundation
- **Tenants Table**: Core tenant management with subscription tiers
  - Subscription tiers: community, professional, enterprise
  - License key assignment
  - Tenant-specific settings and feature flags
  - Data retention and storage quota configuration
  - Custom domain support
- **Default System Tenant**: Backwards compatibility via `tenant_id=1`
  - All existing data automatically belongs to System tenant
  - Single-tenant deployments work unchanged
  - Zero migration required for existing users

#### Portal Users System
- **Portal Users Table**: New enterprise user model separate from identities
  - Tenant-scoped users with email authentication
  - Global roles: admin, support (platform-wide)
  - Tenant roles: admin, maintainer, reader (tenant-wide)
  - MFA support with TOTP secrets and backup codes
  - Account lockout after failed attempts
  - Email verification tracking
  - Password change tracking
- **Portal User Org Assignments**: Organization-level permissions
  - Assign users to specific organizations within tenant
  - Role-based access: admin, maintainer, reader per organization
  - Enables fine-grained permission control

#### SSO/SAML/SCIM Configuration Tables
- **IdP Configurations Table**: Identity Provider setup
  - Support for SAML and OIDC
  - Global IdP (platform-wide) or tenant-specific IdP
  - Entity ID, SSO/SLO URLs, certificates
  - Attribute mapping configuration
  - Just-in-time (JIT) provisioning settings
  - Default role assignment for new users
- **SCIM Configurations Table**: User provisioning
  - SCIM 2.0 endpoint configuration
  - Bearer token authentication
  - Group sync enablement
  - Last sync tracking

#### 3-Tier Permission Model Architecture
```
Global (Elder Platform)
├── Tenant (Company/Customer)
│   └── Organization (Department/Team)
│       └── Entities, Issues, etc.
```

**Roles at each tier:**

| Tier | Admin | Maintainer | Reader |
|------|-------|------------|--------|
| **Global** | Platform management, all tenants | - | Support access across tenants |
| **Tenant** | Tenant config, users, integrations, all orgs | CRUD all org resources | View all org resources |
| **Organization** | Org settings | CRUD org resources | View org resources |

### 🔧 Database Schema Changes

#### New Tables (5)
1. `tenants` - Core tenant management
2. `portal_users` - Enterprise portal user accounts
3. `portal_user_org_assignments` - User-to-organization role mappings
4. `idp_configurations` - SSO/SAML identity provider setup
5. `scim_configurations` - SCIM provisioning configuration

#### Updated Tables (2)
1. `organizations` - Added `tenant_id` field (default=1)
2. `identities` - Added `tenant_id` field (default=1)

### 📊 Technical Details

#### Tenant Isolation Architecture
- All tenant-scoped tables include `tenant_id` foreign key
- Default value of 1 ensures backwards compatibility
- Prepared for PostgreSQL Row-Level Security (RLS) policies
- Application-level tenant context via JWT claims

#### Portal User vs Identity
- **Portal Users**: Enterprise login accounts for the Elder platform
  - Used for SSO/SAML authentication
  - Supports MFA, account lockout, email verification
  - Tenant-scoped with hierarchical roles
- **Identities**: Discovered/synced users from external systems
  - Synced from LDAP, Google Workspace, cloud providers
  - Used for tracking and audit purposes
  - Not for Elder platform authentication

#### License Model Preparation
- Tenant-based pricing structure ready
- `subscription_tier` field for Community/Professional/Enterprise
- `license_key` field for Enterprise license validation
- Feature flags for tier-specific functionality gating

### 🔍 Breaking Changes

**None.** All changes are fully backwards compatible:
- Existing deployments work unchanged
- `tenant_id` defaults to 1 (System tenant)
- No data migration required
- Existing authentication continues working
- All APIs remain functional

### 📦 Dependencies

**No New Python Dependencies Required**:
- All changes are database schema only
- Service layer implementation deferred to v2.2.x patches
- SSO/SAML libraries (python3-saml) already present

### 🎓 Migration Notes

#### Automatic Migration
PyDAL automatically creates new tables on startup:
```bash
# Restart API to apply schema changes
docker-compose build --no-cache api && docker-compose restart api

# Verify new tables in logs
docker-compose logs api | grep "Tables"
```

#### System Tenant Creation
On first startup with v2.2.0:
- System tenant (id=1) should be created manually or via migration
- All existing organizations/identities already have tenant_id=1 default

```sql
-- Create System tenant if not exists
INSERT INTO tenants (id, name, slug, subscription_tier)
VALUES (1, 'System', 'system', 'community')
ON CONFLICT DO NOTHING;
```

### 🔐 Security Notes

#### Tenant Data Isolation
- Database-level isolation via tenant_id foreign keys
- Application enforces tenant context on all queries
- Prepared for PostgreSQL RLS for additional security
- Cross-tenant access requires Global Admin role

#### Portal User Security
- Password hashing with secure algorithms
- MFA support with TOTP and backup codes
- Account lockout after failed attempts
- Email verification for new accounts
- Session management with timeout support

#### IdP Integration Security
- X.509 certificate validation for SAML
- Signed assertions required
- Encrypted responses supported
- Metadata auto-refresh capability

### 🎯 What's Next (v2.2.x Patches)

**Phase 2: Authentication & Portal Users** (v2.2.1)
- Portal user registration with email verification
- Password requirements and history
- Account lockout logic
- JWT tokens with tenant_id claim
- Session management

**Phase 3: SSO/SAML/SCIM** (v2.2.2)
- SAML 2.0 SP-initiated SSO
- Global and tenant IdP authentication flows
- SCIM 2.0 server implementation
- JIT provisioning

**Phase 4: Audit Logging** (v2.2.3)
- Comprehensive audit log schema
- Compliance reporting (SOC 2, ISO 27001)
- Immutable append-only storage

**Phase 5: Admin Consoles** (v2.2.4)
- Super Admin Console for global management
- Tenant Admin Console for user/integration management
- SSO configuration wizard
- Audit log viewer

### 📝 Files Modified

**Database Schema**:
- `apps/api/models/pydal_models.py` - 5 new tables, 2 updated tables (+150 lines)

**Configuration**:
- `.version` - Updated to 2.2.0

**Documentation**:
- `docs/RELEASE_NOTES.md` - This release documentation
- `.PLAN` - Updated with v2.2.0 implementation plan
- `.FUTURE` - Updated with enterprise features roadmap

### 🧪 Testing

**Completed Tests**:
- ✅ Schema syntax validation
- ✅ Table dependency order verified
- ✅ Foreign key relationships valid
- ✅ Default values for tenant_id ensure backwards compatibility

**Pending Tests** (v2.2.1+):
- Portal user authentication tests
- Tenant isolation tests
- SSO/SAML integration tests
- Permission hierarchy tests

### 📈 Version Scheme

Elder uses semantic versioning with build timestamps:
- **Format**: MAJOR.MINOR.PATCH.BUILD
- **This Release**: 2.2.0
  - **MAJOR=2**: Major architectural changes (multi-tenancy)
  - **MINOR=2**: Enterprise features (portal users, IdP, SCIM)
  - **PATCH=0**: Initial enterprise foundation release

### 🎉 Highlights

- **5 New Database Tables**: Complete enterprise multi-tenancy foundation
- **3-Tier Permission Model**: Global → Tenant → Organization hierarchy
- **Portal Users System**: Enterprise-grade user management
- **SSO/SAML/SCIM Ready**: Database schema for identity federation
- **Zero Breaking Changes**: Fully backwards compatible
- **Foundation for Enterprise**: Ready for v2.2.x feature implementation

### 🙏 Acknowledgments

**Development Timeline**: November 20, 2025
**Major Focus**: Enterprise multi-tenancy foundation, portal users, SSO/SAML/SCIM schema

---

## [2.1.0] - 2025-11-19

### 🚀 Connector Expansion Release - iBoss, vCenter, FleetDM

Major expansion of Elder's connector ecosystem with three new integrations for enterprise security, virtualization, and endpoint management platforms.

### ✨ New Features

#### iBoss Cloud Security Connector
- **Users & Groups Discovery**: Sync users and groups from iBoss cloud security platform
- **Application Usage Visibility**: Track applications users access with usage metrics
- **Web Filtering Policies**: Discover and track security policies with categories and actions
- **Cloud Connectors/Gateways**: Monitor iBoss connectors with status, version, and location
- **Automatic Relationships**: Creates dependencies linking users→groups, users→applications
- **Entity Types**: identity (users/groups), security (policies), network (connectors), compute (applications)
- **Configuration**: API URL, API key, tenant ID, sync interval

#### VMware vCenter Connector
- **Virtual Infrastructure Discovery**: Complete vCenter infrastructure visibility
- **Resources Synced**:
  - Datacenters and Clusters (as organizations)
  - ESXi hosts with hardware specs
  - Virtual machines with CPU, memory, disk details
  - Datastores with capacity metrics
  - Networks and distributed port groups
- **Hierarchical Organization**: vCenter → Datacenter → Cluster structure
- **Entity Types**: compute (hosts/VMs), storage (datastores), network (networks)
- **pyVmomi Integration**: Uses official VMware Python SDK
- **Configuration**: Host, port, username, password, SSL verification, sync interval

#### FleetDM Endpoint Management Connector
- **Endpoint Discovery**: Sync managed hosts with detailed hardware/software inventory
- **Software Inventory**: Track all installed software across fleet with version info
- **Vulnerability Tracking**: CVE discovery with CVSS-based severity mapping
  - Critical: CVSS >= 9.0
  - High: CVSS >= 7.0
  - Medium: CVSS >= 4.0
  - Low: CVSS < 4.0
- **Policy Compliance**: Track policy pass/fail rates across fleet
- **Team Support**: FleetDM teams synced as sub-organizations
- **Automatic Relationships**: Creates dependencies linking vulnerabilities→hosts, software→hosts
- **Entity Types**: compute (hosts/software), security (vulnerabilities/policies)
- **Configuration**: URL, API token, sync interval

### 📖 Documentation

#### New Documentation
- **docs/CONNECTORS.md**: Comprehensive connector documentation (460+ lines)
  - All 8 connectors documented (AWS, GCP, Google Workspace, Kubernetes, LDAP, iBoss, vCenter, FleetDM)
  - Configuration examples with environment variables
  - Resources synced per connector
  - Required permissions and IAM policies
  - Entity type mappings
  - Troubleshooting guide
  - Prometheus metrics reference

### 🔧 Configuration

#### New Environment Variables

**iBoss Connector**:
```bash
IBOSS_ENABLED=true
IBOSS_API_URL=https://api.iboss.com
IBOSS_API_KEY=<api-key>
IBOSS_TENANT_ID=<tenant-id>
IBOSS_SYNC_INTERVAL=3600
```

**vCenter Connector**:
```bash
VCENTER_ENABLED=true
VCENTER_HOST=vcenter.example.com
VCENTER_PORT=443
VCENTER_USERNAME=administrator@vsphere.local
VCENTER_PASSWORD=<password>
VCENTER_VERIFY_SSL=true
VCENTER_SYNC_INTERVAL=3600
```

**FleetDM Connector**:
```bash
FLEETDM_ENABLED=true
FLEETDM_URL=https://fleet.example.com
FLEETDM_API_TOKEN=<api-token>
FLEETDM_SYNC_INTERVAL=3600
```

### 📊 Technical Details

#### Connector Architecture
- All connectors follow `BaseConnector` pattern with:
  - `connect()` - Establish API/service connection
  - `disconnect()` - Clean up resources
  - `sync()` - Synchronize resources to Elder
  - `health_check()` - Verify connectivity
- Async/await throughout for performance
- `ElderAPIClient` for entity/organization management
- Organization caching to reduce API calls
- Comprehensive error handling and logging

#### Entity Classification

| Connector | Resource | Entity Type | Sub-Type |
|-----------|----------|-------------|----------|
| iBoss | Users | identity | employee |
| iBoss | Groups | identity | group |
| iBoss | Policies | security | - |
| iBoss | Connectors | network | - |
| vCenter | VMs | compute | virtual_machine |
| vCenter | Hosts | compute | physical_machine |
| vCenter | Datastores | storage | block_storage |
| vCenter | Networks | network | - |
| FleetDM | Hosts | compute | - |
| FleetDM | Vulnerabilities | security | - |
| FleetDM | Policies | security | - |

### 📦 Dependencies

**New Optional Requirements**:
- `pyVmomi` - VMware vSphere API Python bindings (for vCenter connector)

**Existing Dependencies** (already present):
- `httpx` - Async HTTP client (for iBoss, FleetDM)

### 📝 Files Added/Modified

**New Files**:
- `apps/worker/connectors/iboss_connector.py` (454 lines)
- `apps/worker/connectors/vcenter_connector.py` (500+ lines)
- `apps/worker/connectors/fleetdm_connector.py` (489 lines)
- `docs/CONNECTORS.md` (460+ lines)

**Modified Files**:
- `apps/worker/config/settings.py` - Added 15 new configuration fields
- `.version` - Updated to 2.1.0

### 🔍 Breaking Changes

None. All changes are backward compatible:
- New connectors are disabled by default
- Existing connectors continue to work unchanged
- No database schema changes required

### 🎯 Connector Summary

Elder now supports **8 connectors** for comprehensive infrastructure discovery:

| Connector | Platform | Primary Resources |
|-----------|----------|-------------------|
| AWS | Amazon Web Services | EC2, RDS, ElastiCache, SQS, S3, Lambda, EKS |
| GCP | Google Cloud Platform | Compute Engine, Cloud SQL, GKE, Cloud Functions |
| Google Workspace | Google Workspace | Users, Groups, OUs |
| Kubernetes | Kubernetes | Namespaces, Pods, Services, Secrets, PVCs, RBAC |
| LDAP | LDAP/Active Directory | Users, Groups, OUs |
| **iBoss** | iBoss Cloud Security | Users, Groups, Policies, Connectors |
| **vCenter** | VMware vCenter | VMs, Hosts, Datastores, Clusters, Networks |
| **FleetDM** | FleetDM | Hosts, Vulnerabilities, Policies |

### 🙏 Acknowledgments

**Development Timeline**: November 19, 2025
**Major Focus**: Enterprise connector expansion, security platform integration, endpoint management

---

## [2.0.0] - 2025-10-30

### 🚀 Major Architectural Release - IAM Unification, Dedicated Networking, Enhanced Security

**BREAKING CHANGES**: This major release introduces significant architectural improvements with new database schema, unified IAM model, dedicated networking resources, and enhanced secrets/keys management.

### ✨ New Features

#### Unified IAM Model
- **IAM Providers Table**: New `iam_providers` table for centralized identity provider management
  - Support for AWS IAM, GCP IAM, Kubernetes RBAC, Azure Active Directory
  - Organization-scoped provider configuration
  - Provider enable/disable controls
  - Last sync timestamp tracking
- **Google Workspace Providers Table**: New `google_workspace_providers` table for Google Workspace integration
  - Domain and admin email configuration
  - Service account credential management
  - Organization-level workspace instances
- **Identity Type Validation**: Enhanced `identities` table with validated identity types
  - Enum validation: employee, vendor, bot, serviceAccount, integration, otherHuman, other
  - Enables proper identity classification across all IAM sources
  - Foundation for unified IAM dashboard

#### Dedicated Networking Resources Model
- **Network Resources Table**: New `networking_resources` table separate from generic entities
  - 12 network types: subnet, firewall, proxy, router, switch, hub, tunnel, route_table, vrrf, vxlan, vlan, namespace, other
  - Hierarchical network structure with parent_id relationships
  - Geographic and organizational context: region, location, POC, organizational_unit
  - Operational status tracking with status_metadata
  - Full lifecycle management (is_active, created_at, updated_at)
- **Network Entity Mappings Table**: New `network_entity_mappings` table for entity-network relationships
  - Links networking resources to compute/storage/security entities
  - Relationship types: attached, routed_through, connected_to, secured_by, other
  - Metadata field for additional relationship context
- **Network Topology Table**: New `network_topology` table for network connections
  - Source and target network resource connections
  - Connection types: peering, transit, vpn, direct_connect, routing, switching, other
  - Bandwidth and latency metadata
  - Foundation for Visio-style network diagrams

#### Built-in Secrets Management
- **Built-in Secrets Table**: New `builtin_secrets` table for in-app secret storage
  - PyDAL password field encryption for simple string secrets
  - JSON field support for complex credential structures
  - Secret types: api_key, password, certificate, ssh_key, json_credential, other
  - Organization-scoped with expiration support
  - Tags for categorization and search
  - Designed for initial credentials and credential loop scenarios

#### Enhanced Secret & Key Provider Support
- **Hashicorp Vault Integration**: Added to both secret_providers and key_providers
  - Vault secrets engine support
  - Vault Transit engine for key management
  - Production-grade secret management option
- **Provider Options Expanded**:
  - **Secrets**: AWS Secrets Manager, GCP Secret Manager, Infisical, Hashicorp Vault
  - **Keys**: AWS KMS, GCP KMS, Infisical, Hashicorp Vault

#### Discovery Credential Integration
- **Credential Fields**: Extended `discovery_jobs` table with credential support
  - `credential_type`: secret, key, builtin_secret, static, none
  - `credential_id`: Reference to credentials (secret/key/builtin_secret ID)
  - `credential_mapping`: JSON field for mapping secret keys to discovery config fields
- **Flexible Credential Sources**:
  - Use secrets from AWS Secrets Manager, GCP, Infisical, Vault, or built-in
  - Use keys from AWS KMS, GCP KMS, Infisical, or Vault
  - Static credentials via config_json (legacy support)
  - Credential mapping for complex credential structures

### 🔧 Database Schema Changes

#### New Tables (6)
1. `iam_providers` - Centralized IAM provider management
2. `google_workspace_providers` - Google Workspace instances
3. `networking_resources` - Dedicated networking model
4. `network_entity_mappings` - Network-entity relationships
5. `network_topology` - Network connections and topology
6. `builtin_secrets` - In-app encrypted secrets storage

#### Updated Tables (4)
1. `identities` - Added validated identity_type enum
2. `secret_providers` - Added hashicorp_vault provider
3. `key_providers` - Added hashicorp_vault provider
4. `discovery_jobs` - Added credential_type, credential_id, credential_mapping fields

### 🐛 Bug Fixes

#### React Query Cache Invalidation (v2.0.0)
- **Universal Cache Refresh Fix**: Implemented standardized React Query cache invalidation pattern across all 19 UI pages
  - **Problem**: Resources not appearing in lists after creation without manual page refresh
  - **Root Cause**: Query cache not invalidating properly, search queries not updating
  - **Solution**: Applied `refetchType: 'all'` pattern to ALL mutations (52 total)
  - **Pattern**:
    ```typescript
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['resource-name'],
        refetchType: 'all'  // Invalidates ALL matching queries
      })
    }
    ```
  - **Pages Fixed**: Organizations, Networking, Entities, Issues, Projects, Dependencies, Labels, Webhooks, Secrets, Keys, Milestones, IAM, Backups, Discovery, EntityDetail, IssueDetail, OrganizationDetail, Profile, and all their modals
  - **Result**: Immediate UI updates for all CRUD operations without manual refresh

#### UI/UX Improvements
- **Login Page Enhancement**: Added Elder penguin logo (`elder-logo.png`) above login form
- **Networking Modal Fix**: Added missing organization selector to network creation modal
  - Previously required organization_id but had no field to select it
  - Now includes organization dropdown with all available organizations

### 🔧 Configuration Changes

#### Port Configuration Update
- **WEB_PORT**: Changed from 3000 → 3005 (avoiding common port conflicts)
- **GRPC_WEB_PORT**: Changed from 8080 → 8085 (avoiding common port conflicts)
- **CORS_ORIGINS**: Added http://localhost:3005 to allowed origins
- **Reason**: Default ports 3000 and 8080 frequently conflict with other development services

### 📊 Technical Improvements

#### Database Architecture
- **Total New Fields**: 50+ across all new and updated tables
- **PyDAL Auto-Migration**: All schema changes auto-migrated (migrate=True)
- **Backward Compatibility**: Existing data preserved, new fields nullable where appropriate

#### Security Enhancements
- **PyDAL Password Encryption**: Built-in secrets use PyDAL's password field for automatic encryption
- **Credential Separation**: Discovery credentials separate from configuration
- **Provider Flexibility**: Multiple secret managers reduce single-point-of-failure risks
- **Identity Classification**: Proper identity type tracking for security audit trails

#### Networking Improvements
- **Hierarchical Networks**: Support for complex network topologies
- **Entity Separation**: Networks no longer mixed with compute/storage in entities table
- **Regional Context**: Geographic and organizational metadata for network planning
- **Topology Tracking**: Bandwidth, latency, and connection type metadata

#### IAM Improvements
- **Multi-Provider Support**: Unified model for AWS, GCP, Azure AD, Kubernetes, Google Workspace
- **Organization Scoping**: Providers configured per organization
- **Sync Tracking**: Last sync timestamps for freshness monitoring
- **Type Safety**: Identity type validation prevents data quality issues

### 🔍 Breaking Changes

#### 1. Identity Type Validation
- **Impact**: `identities.identity_type` field now has enum validation
- **Required Action**: Invalid identity_type values must be migrated
- **Migration**: Set invalid values to 'other' or map to correct enum
- **SQL Example**:
  ```sql
  UPDATE identities
  SET identity_type = 'other'
  WHERE identity_type NOT IN ('employee', 'vendor', 'bot', 'serviceAccount', 'integration', 'otherHuman', 'other');
  ```

#### 2. Network Resources Separation
- **Impact**: Network entities should migrate from `entities` to `networking_resources` table
- **Required Action**: Run migration script (provided separately)
- **Backward Compatibility**: Old network entities remain in entities table with deprecation notice
- **Timeline**: v2.1.0 will remove deprecated network entities from entities table

#### 3. Secret & Key Provider Updates
- **Impact**: Applications using secret_providers or key_providers need code updates
- **Required Action**: Update provider validation lists in application code
- **New Providers**: hashicorp_vault added to both lists

### 📦 Dependencies

**No New Python Dependencies Required**:
- PyDAL already includes password field encryption
- Hashicorp Vault, Infisical clients deferred to Phase 5-8 implementation
- Database schema changes only - service layer implementation deferred

### 🎓 Migration Notes

#### Database Migration
```bash
# PyDAL auto-migrates schema automatically
docker-compose restart api

# Verify migration in logs
docker-compose logs api | grep "Tables"
```

#### Identity Type Backfill (Optional)
```python
# Run after deployment to clean up invalid identity types
from apps.api.models import db

invalid_identities = db(
    ~db.identities.identity_type.belongs(['employee', 'vendor', 'bot',
                                          'serviceAccount', 'integration',
                                          'otherHuman', 'other'])
).select()

for identity in invalid_identities:
    identity.update_record(identity_type='other')

print(f"Updated {len(invalid_identities)} identities to 'other' type")
```

#### Network Entity Migration (Deferred)
- Migration script will be provided in subsequent release
- Current network entities remain in entities table (deprecated)
- New network discoveries should use networking_resources table
- v2.1.0 will include complete migration tooling

### 🔐 Security Notes

#### Built-in Secrets
- **Encryption**: PyDAL password field provides automatic encryption
- **Best Practice**: Use external secret managers (AWS, GCP, Vault, Infisical) for production
- **Use Cases**:
  - Initial bootstrap credentials
  - Breaking credential loops (e.g., secrets to access secret manager)
  - Development/testing environments
- **Not Recommended For**:
  - Production secrets at scale
  - Highly sensitive credentials
  - Compliance-sensitive data (use Vault/cloud providers instead)

#### Hashicorp Vault Support
- **Enterprise-Grade**: Production-ready secret management
- **Transit Engine**: Key management and cryptographic operations
- **Audit Logging**: Vault provides comprehensive audit trails
- **High Availability**: Vault supports HA deployments
- **Recommendation**: Use Vault for production deployments requiring compliance (SOC2, HIPAA, PCI-DSS)

#### Discovery Credentials
- **Separation of Concerns**: Credentials separate from discovery configuration
- **Credential Rotation**: Easier credential updates without modifying discovery config
- **Provider Flexibility**: Switch credential sources without changing discovery logic
- **Mapping Support**: Complex credentials (JSON) mapped to discovery fields

### 📝 Files Modified

**Database Schema**:
- `apps/api/models/pydal_models.py` - 6 new tables, 4 updated tables (+115 lines)

**Documentation**:
- `.version` - Updated to 2.0.0.1761863458
- `.PLAN` - Updated with Phase 1-4 completion status
- `docs/RELEASE_NOTES.md` - This comprehensive release documentation

### 🎯 What's Next (Deferred to v2.0.x)

The following phases are deferred for iterative implementation:

**Phase 5-6: Backend Services** (v2.0.1)
- Built-in secrets service implementation
- Hashicorp Vault client implementation
- Infisical client enhancements
- IAM service updates for new providers

**Phase 7-8: Backend Services** (v2.0.2)
- Networking service (CRUD, topology, visualization)
- Network discovery integration with connectors
- Discovery credential resolution service
- IAM unified service (multi-provider aggregation)

**Phase 9: REST API Endpoints** (v2.0.3)
- `/api/v1/networking` - Full CRUD for network resources
- `/api/v1/iam` - Unified IAM endpoints (providers, identities, groups)
- Enhanced `/api/v1/secrets` - Built-in secrets support
- Enhanced `/api/v1/discovery` - Credential configuration

**Phase 10-12: Frontend** (v2.0.4)
- IAM unified page (tabbed interface for all IAM sources)
- Networking page with Visio-style visualization
- Discovery credential configuration UI
- Secrets management enhancements

### 🏗️ Development Approach

This release follows a **database-first, incremental implementation** strategy:

1. **v2.0.0** (This Release): Database schema foundation
   - All tables created and migrated
   - Schema validated and tested
   - No breaking changes to existing functionality

2. **v2.0.1-v2.0.4** (Future): Service and UI layers
   - Backend services implemented incrementally
   - REST APIs added progressively
   - Frontend components built iteratively
   - Each release fully tested and production-ready

3. **Benefits**:
   - Clear git history with focused commits
   - Easier rollback if issues discovered
   - Incremental testing and validation
   - Reduced deployment risk

### 🧪 Testing

**Completed Tests**:
- ✅ API builds successfully
- ✅ API starts with state "Up (healthy)"
- ✅ Database schema auto-migrated via PyDAL
- ✅ No startup errors
- ✅ Health check responding: GET /healthz HTTP/1.1 200 OK
- ✅ All existing endpoints functional

**Pending Tests** (v2.0.1+):
- Backend service unit tests
- API endpoint integration tests
- Frontend E2E tests
- Network visualization tests
- Migration script testing

### 📈 Version Scheme

Elder uses semantic versioning with build timestamps:
- **Format**: MAJOR.MINOR.PATCH.BUILD
- **This Release**: 2.0.0.1761863458
  - **MAJOR=2**: Breaking changes (identity_type validation, networking separation)
  - **MINOR=0**: New features (IAM providers, networking resources, built-in secrets)
  - **PATCH=0**: Initial major release
  - **BUILD=1761863458**: Epoch64 timestamp (Oct 30, 2025 22:37:38 UTC)

### 🎉 Highlights

- **6 New Database Tables**: Comprehensive new data models
- **50+ New Fields**: Enhanced metadata and relationships
- **4 Updated Tables**: Improved validation and features
- **Zero Downtime**: PyDAL auto-migration preserves all data
- **Backward Compatible**: Existing functionality unchanged
- **Foundation for v2.x**: Database ready for service layer implementation

### 🙏 Acknowledgments

**Development Timeline**: October 30, 2025
**Major Focus**: Database architecture, IAM unification, networking separation, security enhancements
**Git Commit**: 2cc2e6f - Feature: Elder v2.0.0 Database Schema

---

## [1.2.1] - 2025-10-29

### 🚀 Cloud Infrastructure Expansion Release

Major expansion of Elder's cloud infrastructure discovery capabilities with support for additional AWS services, Kubernetes/container orchestration, and comprehensive status tracking for operational visibility.

### ✨ New Features

#### Database Schema Enhancements
- **Status Metadata Tracking**: Added `status_metadata` JSON field to entities table
  - Captures operational status (Running, Stopped, Deleted, Creating, Error)
  - Includes epoch64 timestamp for status updates
  - Enables real-time operational monitoring and historical tracking
- **Namespace Sub-Entity Type**: Added `namespace` as network entity sub-type for Kubernetes support
  - Metadata template includes cluster, resource_quota, labels, annotations

#### AWS Connector Extensions
- **RDS/Aurora Database Discovery**:
  - Automatic Aurora detection (engine starts with "aurora")
  - Comprehensive database metadata (engine, version, instance class, storage)
  - Multi-AZ and encryption status tracking
  - Database endpoints and VPC associations
  - Status tracking: Available, Creating, Deleting, Failed, etc.
- **ElastiCache Cluster Discovery**:
  - Redis and Memcached cluster support
  - Node type, count, and endpoint information
  - Availability zone and VPC integration
  - Status tracking for cache lifecycle management
- **SQS Queue Discovery**:
  - Standard and FIFO queue detection
  - Queue configuration (retention, visibility timeout, delays)
  - Approximate message count monitoring
  - Queue ARN and URL tracking
- **Enhanced Status Tracking**: All AWS resources (EC2, VPCs, RDS, ElastiCache, SQS) now use status_metadata field

#### Kubernetes Connector (NEW)
- **Multi-Cluster Support**: Automatic cluster detection (in-cluster or kubeconfig)
- **Namespace Discovery**:
  - Resource quota tracking (CPU, memory, pods)
  - Labels, annotations, and UID tracking
  - Active/Terminating status monitoring
- **Secret Discovery** (Security-Focused):
  - Metadata-only approach - NEVER exposes secret values
  - Tracks secret names, types, and key counts
  - Security entity type classification
  - Helps identify secret sprawl without compromising security
- **Pod/Container Discovery**:
  - Running container inventory
  - Container images and port mappings
  - Node assignment and IP addresses
  - Pod lifecycle tracking (Running, Pending, Failed, etc.)
- **Persistent Volume Claim Discovery**:
  - Storage provisioning monitoring
  - Capacity vs requested storage tracking
  - Access modes and storage classes
  - Bound/Pending status tracking
- **Compatible with All K8S Distributions**: EKS, GKE, AKS, OpenShift, Rancher, K3s, Vanilla K8S

#### Backup System Enhancements
- **Per-Job S3 Configuration**:
  - Configure different S3 buckets per backup job
  - Job-specific S3 credentials and endpoints
  - Support for MinIO, Wasabi, Backblaze B2, DigitalOcean Spaces
  - Fallback to global S3 configuration
- **Web UI S3 Configuration**:
  - Expandable S3 configuration section in backup job creation
  - 6 input fields: endpoint, bucket, region, access key, secret key, prefix
  - Toggle checkbox to enable/disable S3 per job
  - Inline validation and helper text

### 🔧 Technical Improvements

#### Data Models
- **Entity Model Updates** (pydal_models.py):
  - Added `status_metadata` JSON field for operational status tracking
  - Field automatically migrated to existing entities (nullable)
- **Entity Types** (entity_types.py):
  - Added `NAMESPACE` constant to NetworkSubType
  - Created namespace metadata template with K8S-specific fields
  - Updated ENTITY_SUBTYPES mapping
- **Connector Client** (elder_client.py):
  - Extended Entity dataclass with `sub_type` and `status_metadata` fields
  - Support for v1.2.1 entity classification system

#### AWS Connector Architecture
- **Three New Sync Methods**:
  - `_sync_rds_instances()` - RDS and Aurora databases
  - `_sync_elasticache_clusters()` - Redis/Memcached caches
  - `_sync_sqs_queues()` - Message queues
- **Status Integration**: All sync methods capture real-time status with timestamps
- **Proper Entity Classification**:
  - RDS → storage/database
  - ElastiCache → storage/caching
  - SQS → storage/queue_system

#### Kubernetes Connector Architecture
- **Four Resource Sync Methods**:
  - `_sync_namespaces()` - Network namespace entities
  - `_sync_secrets()` - Security/config entities (metadata only)
  - `_sync_pods()` - Compute/kubernetes_node entities
  - `_sync_pvcs()` - Storage/virtual_disk entities
- **Security Best Practices**: Secret discovery NEVER exposes values
- **Cluster-Level Organization**: Hierarchical organization structure per cluster

#### Backup Service Architecture
- **Per-Job S3 Override Pattern**:
  - Temporary configuration override during backup execution
  - Job-specific S3 settings with fallback to global
  - Original configuration restoration after upload
- **Database Schema**: 7 new S3 fields in backup_jobs table
- **API Integration**: Extended create_backup_job endpoint with S3 parameters

### 📊 API Enhancements

- **Backup API**:
  - Enhanced `POST /api/v1/backup/jobs` to accept 7 S3 configuration parameters
  - Per-job S3 override support
- **Entity Types API**: Namespace now available as valid network sub-type

### 🎯 Integration Benefits

#### Operational Visibility
- **Real-Time Status Monitoring**: Track resource states during discovery syncs
- **Historical Tracking**: Timestamp-based status history
- **Lifecycle Management**: Identify stuck, failed, or transitioning resources

#### Comprehensive Cloud Coverage
Elder now discovers and tracks:
- **AWS**: EC2, VPCs, S3, RDS, Aurora, ElastiCache, SQS
- **Kubernetes**: Namespaces, Secrets, Pods, Containers, PVCs
- **All major K8S distributions**: EKS, GKE, AKS, OpenShift, Rancher

#### Enhanced Classification
- **Entity Sub-Types**: Proper classification (database, caching, queue_system, namespace, kubernetes_node)
- **Entity Types**: Correct categorization (storage, network, compute, security)
- **Status Metadata**: Uniform status tracking across all cloud resources

#### Flexible Backup Strategy
- **Multi-Destination Backups**: Different backup jobs → different S3 buckets
- **Provider Flexibility**: Mix AWS S3, MinIO, Wasabi, Backblaze B2 per job
- **Global + Local Configuration**: Per-job overrides with global defaults

### 📝 Files Modified

**Models & Schema**:
- `apps/api/models/pydal_models.py` - Added status_metadata field to entities
- `apps/api/models/entity_types.py` - Added namespace sub-type and metadata

**Connectors**:
- `apps/worker/connectors/aws_connector.py` - Added RDS, ElastiCache, SQS sync methods
- `apps/worker/connectors/k8s_connector.py` - NEW: Complete Kubernetes connector (518 lines)
- `apps/worker/utils/elder_client.py` - Extended Entity dataclass

**Backup System**:
- `apps/api/api/v1/backup.py` - Enhanced create_backup_job endpoint
- `apps/api/services/backup/service.py` - Per-job S3 configuration support
- `web/src/pages/Backups.tsx` - S3 configuration UI

**Documentation**:
- `docs/README.md` - Added Backup & Data Management section
- `docs/RELEASE_NOTES.md` - This file

### 🔍 Breaking Changes

None. All changes are backward compatible:
- Existing entities work without status_metadata (nullable field)
- Global S3 configuration still works for backup jobs
- Existing AWS connector resources continue to sync normally

### 📦 Dependencies

**New Requirements**:
- `kubernetes` - Python Kubernetes client library (for K8S connector)

**Existing Dependencies** (already present):
- `boto3` - AWS SDK (extended usage for RDS, ElastiCache, SQS)
- `botocore` - AWS core library

### 🎓 Migration Notes

#### Database Migration
PyDAL automatically migrates the schema (migrate=True):
- `status_metadata` field added to entities table
- No manual migration required
- Existing entities remain unchanged (null status_metadata)

#### Backup Jobs
Existing backup jobs without S3 configuration:
- Continue using global S3 settings (if configured)
- Can be updated to use per-job S3 settings via API or UI

#### Kubernetes Integration
To enable K8S discovery:
1. Install kubernetes library: `pip install kubernetes`
2. Configure kubeconfig or run connector in-cluster
3. Set appropriate RBAC permissions for resource discovery
4. Run connector with K8S support enabled

### 🔐 Security Notes

#### Kubernetes Secrets
- Secret values are NEVER retrieved from K8S API
- Only metadata and key names are stored
- Provides inventory visibility without compromising security
- Helps identify secret sprawl and unused secrets

#### S3 Credentials
- Per-job S3 credentials stored in database
- Consider using Secret Manager integration (future enhancement)
- Credentials encrypted at rest (database-level encryption recommended)

---

## [1.0.0] - 2025-10-25

### 🎉 Production Release - v1.0.0

First production-ready release of Elder with comprehensive UI/UX improvements, enhanced issue management, and complete branding integration.

### ✨ New Features

#### Enhanced Issue Creation
- **Organization/Entity Assignment**: Issues can now be assigned to either an organization OR entities (mutually exclusive)
- **Radio button toggle** for choosing between Organization and Entity assignment modes
- **Multi-select entity assignment**: Ability to assign issues to multiple entities simultaneously
- **Label selection during creation**: Apply multiple labels when creating an issue via checkbox list
- **Visual label indicators**: Colored dots showing label colors in selection interface
- **Scrollable selection lists**: Clean UI with max-height containers for large entity/label lists

#### UI/UX Improvements
- **Organization Type Management**:
  - Edit organization type (Department, Organization, Team, Collection, Other) via modal
  - Type field displayed in organization detail information card
  - Type selection dropdown in create/edit organization modals
- **Clickable Dashboard Items**:
  - Recent Organizations list items now navigate to organization detail pages
  - Recent Entities list items now navigate to entity detail pages
  - Hover effects and visual feedback on all clickable items
- **Anchor Link Navigation**:
  - Overview bubbles now scroll to corresponding sections
  - Smooth scroll behavior for better UX
  - Section IDs for Issues, Projects, Identities, and Hierarchy
  - Quick navigation from statistics to detailed views

#### Branding & Visual Identity
- **Elder Logo Integration**:
  - Elder-Logo.png used as sidebar logo (48px height)
  - Elder-Logo.png used as browser favicon
  - Professional branding throughout the application
  - Consistent visual identity across all pages

#### Modal-First UI Pattern
- **Edit Organization Modal**: Inline editing without navigation disruption
- Follows CLAUDE.md modal-first approach for secondary actions
- Keeps users focused on main organizational views
- Improved workflow efficiency

### 🐛 Bug Fixes

- **Async Decorator Support**: Fixed metadata endpoint 500 errors by making `@resource_role_required` and `@org_permission_required` decorators async-aware
- **Network Graph Edge Rendering**: Added `Handle` components to ReactFlow custom nodes to fix missing edge visualization
- **Edge Direction**: Implemented proper top-to-bottom hierarchical flow for organizational charts
- **Tree Depth Calculation**: Recursive algorithm for accurate multi-level organization hierarchies
- **Entity Positioning**: Entities now correctly placed one level below their containing organization

### 🔧 Technical Improvements

#### Frontend (React/TypeScript)
- Added `organization_type` field to Organization interface
- Created `OrganizationType` type definition
- Enhanced CreateIssueModal with assignment type selection
- Implemented entity and label multiselect components
- Added smooth scroll functionality for section navigation
- React Query integration for entities and labels fetching

#### Backend (Flask/Python)
- Async/await support in authentication decorators
- Proper coroutine handling with `inspect.iscoroutinefunction()`
- Support for `entity_ids` and `label_ids` in issue creation API

#### Network Graph
- Custom ReactFlow nodes with proper Handle components
- Top-to-bottom layout with configurable spacing
- Tree depth calculation for hierarchical positioning
- Colored edges based on relationship types
- Animated edges with arrow markers

### 📊 API Enhancements

- **Issues API**: Enhanced createIssue endpoint to support:
  - `entity_ids` array for multi-entity assignment
  - `label_ids` array for label application
  - Mutually exclusive `organization_id` or `entity_ids`
- **Organizations API**: Support for `organization_type` field in create/update operations

### 🎨 UI Components

- **EditOrganizationModal**: Full organization editing with type selection
- **CreateIssueModal**: Comprehensive issue creation with assignment and label options
- **Radio Button Groups**: Clean assignment type selection
- **Checkbox Lists**: Multi-select for entities and labels
- **Scrollable Containers**: Better handling of long lists
- **Visual Indicators**: Label color dots and hover effects

### 📦 Container Updates

- Web container rebuilt with all UI/UX improvements
- Elder logo properly integrated into build pipeline
- Multi-stage builds optimized for production
- Nginx serving static assets efficiently

### 🔄 Upgrade Notes from v0.1.0

1. **Database**: No schema changes required
2. **API**: Backward compatible - new optional fields in issue creation
3. **Frontend**: Complete rebuild - clear browser cache after update
4. **Assets**: New logo file added to public directory

### 🙏 Acknowledgments

This release represents a significant UI/UX milestone, transforming Elder from a functional system into a polished, production-ready enterprise application.

**Development Timeline**: October 23-25, 2025
**Major Focus**: User experience, visual polish, and workflow optimization

---

## [0.1.0] - 2024-10-23

### 🎉 Initial Release

First production-ready release of Elder - Entity, Element, and Relationship Tracking System.

### ✨ Features Added

#### Core Infrastructure (Phase 1)
- Database models for Organizations, Entities, Dependencies, Identities, and RBAC
- SQLAlchemy ORM with Alembic migrations
- PostgreSQL 15+ and Redis 7+ integration
- Unique 64-bit ID generation for all entities
- Hierarchical organization structures with unlimited nesting
- Six entity types: Datacenters/VPCs, Subnets, Compute, Network, Users, Security Issues
- Metadata support (JSONB) for all entities and organizations

#### REST API (Phase 2)
- 79 RESTful API endpoints with OpenAPI 3.0 documentation
- Organizations API (9 endpoints): Full CRUD + hierarchy operations
- Entities API (8 endpoints): Full CRUD + dependency tracking
- Dependencies API (9 endpoints): Full CRUD + bulk operations
- Graph API (3 endpoints): Visualization with filters
- Authentication API (7 endpoints): Login, logout, refresh, token management
- Identities API (13 endpoints): User and group management
- Lookup API (2 endpoints): Unique ID lookups
- Pagination, filtering, and field selection support
- Request validation with marshmallow schemas
- Rate limiting with Flask-Limiter

#### Authentication & Authorization (Phase 3)
- JWT token-based authentication (access + refresh tokens)
- SAML 2.0 SSO integration (python3-saml)
- OAuth2 provider integration (Authlib)
- Local authentication with password hashing
- Multi-factor authentication support
- Role-Based Access Control (RBAC) with 4 global roles
- Organization-scoped permissions
- Permission decorators (@login_required, @permission_required)
- Audit logging for all authentication events

#### Web UI (Phase 4)
- Bootstrap 5.3.2 responsive interface
- Interactive dependency graph visualization (vis.js Network 9.1.9)
- 22 web routes covering full application functionality
- Dashboard with real-time statistics
- Organization management UI (list, create, edit, delete)
- Entity management UI
- Graph visualization with filters (org, type, depth)
- Authentication pages (login, register, logout)
- License tier display in navigation
- Dark mode CSS support

#### gRPC API (Phase 5 - Enterprise)
- High-performance gRPC server with 45 RPC methods
- Protocol Buffers 3 (proto3) schema definitions
- gRPC-Web support via Envoy proxy
- Enterprise license validation requirement
- 100MB message size limits
- Server reflection for debugging
- Graceful shutdown handling
- Organization servicers (7 methods fully implemented)
- 5 additional service categories (38 methods stubbed)

#### Enterprise Features (Phase 6)
- **GitHub-Style Issues System**:
  - Issue tracking with labels, priorities, assignments
  - Comment threads and entity linking
  - 14 REST endpoints with role-based permissions
  - Default issue labels (bug, enhancement, documentation, etc.)
- **Resource-Level Roles**:
  - Maintainer, Operator, Viewer roles per entity/org
  - 6 REST endpoints for role management
  - Hierarchical permission checking
- **Typed Metadata**:
  - Custom metadata fields with type validation
  - Support for string, number, date, boolean, JSON types
  - System metadata (read-only) support
  - 8 REST endpoints (4 for entities, 4 for organizations)

#### Infrastructure & DevOps (Phase 8)
- **GitHub Actions CI/CD**:
  - Comprehensive test pipeline (lint, security, unit, integration)
  - Multi-arch Docker builds (amd64 + arm64)
  - Security scanning (Trivy + Safety)
  - Code coverage tracking (Codecov)
- **Kubernetes Manifests**:
  - Production-ready deployments
  - Resource limits and health checks
  - Secret management
- **Helm Charts**:
  - Complete Helm chart with 14 templates
  - 50+ configurable parameters
  - Bitnami PostgreSQL and Redis dependencies
  - Ingress, HPA, ServiceMonitor, NetworkPolicy support
  - 400+ line README with examples
- **Structured Logging**:
  - Multi-destination logging (console, syslog, Kafka, CloudWatch, GCP)
  - Verbosity levels (-v, -vv, -vvv)
  - HTTP3/QUIC support for Kafka
  - 600+ line documentation
- **Monitoring**:
  - Prometheus metrics endpoint
  - Custom Grafana dashboard (11 panels)
  - HTTP, gRPC, database, and infrastructure metrics

#### Websites & Documentation (Phase 9)
- **Marketing Website** (elder.penguintech.io):
  - Modern design with subtle purple/indigo gradients
  - Multi-page structure (Home, Features, Pricing)
  - Fully responsive design
  - Interactive SVG graph visualization
  - 3 HTML pages, 600+ lines of CSS
- **Documentation Website** (elder-docs.penguintech.io):
  - MkDocs Material theme
  - 8-section navigation structure
  - Quick start guide (5-minute setup)
  - License tier information
  - Dark/light mode toggle

#### Testing & Quality (Phase 10)
- Unit tests for models (Organization, Entity)
- Unit tests for API endpoints (Organizations)
- Integration tests for complete workflows
- Comprehensive test fixtures and mocking
- 80%+ code coverage target
- Security-focused testing (no network calls in unit tests)

### 🔐 Security

- TLS 1.2 minimum, TLS 1.3 preferred
- Input validation with marshmallow schemas
- SQL injection prevention via SQLAlchemy ORM
- XSS prevention with Jinja2 auto-escaping
- CSRF protection with Flask-WTF
- Rate limiting to prevent abuse
- Comprehensive audit logging
- Environment variable-based secrets management
- Container vulnerability scanning
- Multi-factor authentication support

### 📦 Deployment

- Docker multi-stage builds with debian-slim base images
- Docker Compose for development and production
- Kubernetes manifests for production deployment
- Helm charts for easy Kubernetes installation
- Multi-architecture support (amd64/arm64)
- Production-ready configuration templates

### 🏢 License Tiers

- **Community**: Up to 100 entities, local auth, basic features
- **Professional** ($99/mo): Unlimited entities, SAML/OAuth2, advanced features
- **Enterprise** (Custom): All features + gRPC API, LDAP sync, 24/7 support

### 📊 API Summary

- **REST Endpoints**: 79 total
  - Organizations: 9
  - Entities: 8
  - Dependencies: 9
  - Graph: 3
  - Auth: 7
  - Identities: 13
  - Lookup: 2
  - Resource Roles: 6
  - Issues: 14
  - Metadata: 8

- **gRPC Methods**: 45 total
  - Authentication: 11
  - Organizations: 7
  - Entities: 7
  - Dependencies: 7
  - Graph: 4
  - Health: 1
  - Resource Roles: 6
  - Issues: 14 (future)
  - Metadata: 8 (future)

### 🛠️ Technical Stack

- **Backend**: Python 3.13, Flask, SQLAlchemy, Alembic
- **APIs**: REST (OpenAPI 3.0), gRPC (protobuf)
- **Database**: PostgreSQL 15+
- **Cache**: Redis 7+
- **Frontend**: HTML5, Bootstrap 5, vis.js Network
- **Auth**: SAML (python3-saml), OAuth2 (Authlib), JWT
- **Monitoring**: Prometheus, Grafana
- **Container**: Docker, docker-compose
- **Orchestration**: Kubernetes, Helm

### 📝 Documentation

- Comprehensive README.md with badges and ASCII art
- Complete .PLAN file documenting all implementation phases
- API documentation via OpenAPI/Swagger
- gRPC API documentation (600+ lines)
- Helm chart README (400+ lines)
- Logging documentation (600+ lines)
- Marketing website with features and pricing
- MkDocs-based documentation site

### 🐛 Known Issues

- gRPC servicers: 40 of 45 methods are stubs (5 fully implemented)
- Sparse checkout submodule not implemented (skipped due to complexity)
- Website deployment pending (sites created but not yet deployed)
- Some enterprise features require additional polish

### 🔄 Upgrade Notes

This is the initial release. No upgrade path required.

### 🙏 Acknowledgments

Built with these excellent open-source projects:
- Flask, SQLAlchemy, gRPC, vis.js, Bootstrap, PostgreSQL, Redis

Developed by **Penguin Tech Inc** - https://www.penguintech.io

---

## Future Releases

### [0.2.0] - Planned

**Optional Enhancements (Phase 6a)**:
- LDAP/SAML group synchronization
- Import/export functionality (JSON, YAML, CSV)
- WebSocket real-time updates for graph changes
- Advanced search and filtering
- Bulk operations
- Complete remaining gRPC servicer implementations

**License Integration Refinement (Phase 7)**:
- Enhanced license server integration
- Keepalive reporting
- License management UI for admins
- Stricter tier enforcement

---

For detailed implementation history, see [.PLAN](.PLAN) in the repository root.

Copyright © 2024 Penguin Tech Inc. All rights reserved.
