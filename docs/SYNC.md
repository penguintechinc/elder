# Elder v1.1.0: Project Management Synchronization

## Overview

Elder v1.1.0 introduces two-way synchronization with external project management platforms, enabling seamless integration between Elder and:

- **GitHub** (Issues, Projects, Milestones, Labels)
- **GitLab** (Issues, Epics, Milestones, Labels)
- **Jira Cloud** (Issues, Projects, Sprints, Components)
- **Trello** (Cards, Boards, Lists, Labels)
- **OpenProject** (Work Packages, Projects, Versions)

## Architecture

### Sync Strategy

- **Primary Method**: Webhooks for real-time updates
- **Fallback Method**: Batch polling when webhooks fail (configurable interval)
- **Conflict Resolution**: Last-modified-wins (timestamp-based)
- **Two-Way Creation**: Optional (default: OFF)
- **Closure Synchronization**: Enabled across all platforms

### Components

1. **Base Sync Framework** (`apps/worker/sync/base.py`)
   - `BaseSyncClient`: Abstract base class for all platforms
   - `SyncOperation`, `SyncResult`, `SyncMapping`: Core data structures
   - Database mapping management

2. **Conflict Resolution** (`apps/worker/sync/conflict_resolver.py`)
   - Last-modified-wins strategy (primary)
   - Elder-wins, External-wins strategies
   - Field-level merge with intelligent detection

3. **Webhook Handler** (`apps/worker/sync/webhook_handler.py`)
   - Unified webhook handling for all platforms
   - Platform-specific signature validation
   - Event parsing and routing

4. **Batch Scheduler** (`apps/worker/sync/batch_scheduler.py`)
   - APScheduler-based async job scheduling
   - Automatic fallback when webhooks timeout
   - Webhook health monitoring

5. **Platform Clients**
   - `github_client.py`: GitHub integration
   - `gitlab_client.py`: GitLab integration
   - `jira_client.py`: Jira Cloud integration
   - `trello_client.py`: Trello integration
   - `openproject_client.py`: OpenProject integration

## Database Schema

### sync_configs

Stores platform sync configurations.

| Field | Type | Description |
|-------|------|-------------|
| id | INTEGER | Primary key |
| name | STRING | Config name (unique) |
| platform | STRING | Platform (github/gitlab/jira/trello/openproject) |
| enabled | BOOLEAN | Whether sync is enabled |
| sync_interval | INTEGER | Sync interval in seconds (default: 300) |
| batch_fallback_enabled | BOOLEAN | Enable batch fallback (default: TRUE) |
| batch_size | INTEGER | Batch size (default: 100) |
| two_way_create | BOOLEAN | Allow creating new items (default: FALSE) |
| webhook_enabled | BOOLEAN | Enable webhooks (default: TRUE) |
| webhook_secret | STRING | Webhook secret for validation |
| config_json | JSON | Platform-specific configuration |
| last_sync_at | DATETIME | Last sync timestamp |
| last_batch_sync_at | DATETIME | Last batch sync timestamp |

### sync_mappings

Stores Elder ↔ External ID mappings.

| Field | Type | Description |
|-------|------|-------------|
| id | INTEGER | Primary key |
| elder_type | STRING | Resource type (issue/project/milestone/label) |
| elder_id | INTEGER | Elder resource ID |
| external_platform | STRING | External platform name |
| external_id | STRING | External resource ID |
| sync_config_id | INTEGER | FK to sync_configs |
| sync_status | STRING | Status (synced/conflict/error/pending) |
| sync_method | STRING | Method (webhook/poll/batch/manual) |
| last_synced_at | DATETIME | Last successful sync |
| elder_updated_at | DATETIME | Elder resource modified time |
| external_updated_at | DATETIME | External resource modified time |

### sync_history

Audit trail of all sync operations.

| Field | Type | Description |
|-------|------|-------------|
| id | INTEGER | Primary key |
| sync_config_id | INTEGER | FK to sync_configs |
| correlation_id | STRING | UUID for distributed tracing |
| sync_type | STRING | Type (webhook/poll/batch/manual) |
| items_synced | INTEGER | Number of items synced |
| items_failed | INTEGER | Number of items failed |
| started_at | DATETIME | Operation start time |
| completed_at | DATETIME | Operation completion time |
| success | BOOLEAN | Whether operation succeeded |
| error_message | TEXT | Error details (if any) |
| sync_metadata | JSON | Additional metadata |

### sync_conflicts

Unresolved sync conflicts requiring manual intervention.

| Field | Type | Description |
|-------|------|-------------|
| id | INTEGER | Primary key |
| mapping_id | INTEGER | FK to sync_mappings |
| conflict_type | STRING | Type (timestamp/field_mismatch/deleted_*) |
| elder_data | JSON | Elder's version of data |
| external_data | JSON | External's version of data |
| resolution_strategy | STRING | Strategy (manual/elder_wins/external_wins/merge) |
| resolved | BOOLEAN | Whether conflict is resolved |
| resolved_at | DATETIME | Resolution timestamp |
| resolved_by_id | INTEGER | FK to identities |

## API Endpoints

### Sync Configuration Management

```bash
# List all sync configurations
GET /api/v1/sync/configs

# Create new sync configuration
POST /api/v1/sync/configs
{
  "name": "GitHub Main Repo",
  "platform": "github",
  "enabled": true,
  "sync_interval": 300,
  "config_json": {
    "api_token": "ghp_...",
    "org_name": "myorg",
    "repo_name": "myrepo"
  }
}

# Get sync configuration
GET /api/v1/sync/configs/:id

# Update sync configuration
PATCH /api/v1/sync/configs/:id

# Delete sync configuration
DELETE /api/v1/sync/configs/:id
```

### Sync History & Status

```bash
# List sync history (paginated)
GET /api/v1/sync/history?page=1&per_page=50&config_id=1

# Get overall sync status
GET /api/v1/sync/status
```

### Conflict Management

```bash
# List unresolved conflicts
GET /api/v1/sync/conflicts?resolved=false

# Resolve a conflict
POST /api/v1/sync/conflicts/:id/resolve
{
  "resolution_strategy": "elder_wins"
}
```

### Sync Mappings

```bash
# List sync mappings
GET /api/v1/sync/mappings?config_id=1&elder_type=issue
```

## Environment Variables

### Batch Fallback Configuration

```bash
SYNC_BATCH_FALLBACK_ENABLED=true  # Enable batch fallback
SYNC_BATCH_INTERVAL=3600          # Batch interval in seconds
SYNC_BATCH_FALLBACK_SIZE=100      # Batch size
```

### Logging Configuration

```bash
# Syslog UDP
SYSLOG_ENABLED=false
SYSLOG_HOST=localhost
SYSLOG_PORT=514

# KillKrill HTTP3/QUIC
KILLKRILL_ENABLED=false
KILLKRILL_URL=https://killkrill.penguintech.io
KILLKRILL_API_KEY=your_api_key
KILLKRILL_USE_HTTP3=true
```

## Platform Configuration

### GitHub

```json
{
  "api_token": "ghp_...",
  "org_name": "your-org",
  "repo_name": "your-repo",
  "base_url": "https://api.github.com"
}
```

### GitLab

```json
{
  "api_token": "glpat-...",
  "project_id": "12345",
  "base_url": "https://gitlab.com/api/v4"
}
```

### Jira Cloud

```json
{
  "api_token": "...",
  "email": "user@example.com",
  "jira_url": "yourcompany.atlassian.net",
  "project_key": "PROJ"
}
```

### Trello

```json
{
  "api_key": "...",
  "api_token": "...",
  "board_id": "abc123"
}
```

### OpenProject

```json
{
  "api_key": "...",
  "base_url": "https://yourcompany.openproject.com",
  "project_id": "123"
}
```

## Multi-Destination Logging

Elder v1.1.0 supports three logging destinations:

1. **Console** (always enabled)
   - Structured JSON or human-readable format
   - Configurable log levels

2. **Syslog UDP** (optional)
   - Traditional syslog protocol
   - Useful for centralized log collection

3. **KillKrill HTTP3/QUIC** (optional)
   - High-performance log streaming
   - Batched delivery with automatic flushing
   - HTTP3/QUIC or HTTP/2 fallback

All logs include correlation IDs for distributed tracing across sync operations.

## Conflict Resolution

### Strategies

1. **Last-Modified-Wins** (Default)
   - Compares timestamps
   - Most recent change wins

2. **Elder-Wins**
   - Elder data takes precedence
   - Use when Elder is source of truth

3. **External-Wins**
   - External platform data takes precedence
   - Use when external system is authoritative

4. **Field-Merge**
   - Intelligent field-level merging
   - Resolves individual field conflicts

5. **Manual**
   - Requires human intervention
   - Used for complex conflicts

### Conflict Detection

Conflicts are detected when:
- Both sides modified since last sync (timestamp conflict)
- Fields have different values (field mismatch)
- Resource deleted on one side but not the other
- Invalid state transitions

## Monitoring

### Prometheus Metrics

- `sync_operations_total{platform, status}`: Total sync operations
- `sync_duration_seconds{platform}`: Sync operation duration
- `sync_conflicts_total{platform, type}`: Total conflicts by type
- `sync_mappings_total{platform}`: Total active mappings

### Health Checks

```bash
# Worker health check
GET http://localhost:8000/healthz
```

## Troubleshooting

### Common Issues

1. **Webhook not receiving events**
   - Check webhook secret configuration
   - Verify firewall/network settings
   - Check webhook URL in external platform

2. **Batch sync not running**
   - Verify `SYNC_BATCH_FALLBACK_ENABLED=true`
   - Check `sync_interval` setting
   - Review scheduler logs

3. **Conflicts not resolving**
   - Review conflict resolution strategy
   - Check timestamps on both sides
   - Use manual resolution for complex cases

4. **Authentication failures**
   - Verify API tokens are valid
   - Check token permissions/scopes
   - Ensure tokens haven't expired

### Debug Logging

Enable debug logging for detailed sync information:

```bash
LOG_LEVEL=DEBUG
```

## Security Considerations

1. **API Tokens**: Store securely in environment variables or secrets manager
2. **Webhook Secrets**: Use strong, unique secrets for each platform
3. **TLS**: Always use HTTPS for API communication
4. **Permissions**: Follow principle of least privilege for API tokens

## Migration Guide

### Enabling Sync for Existing Installations

1. Add sync tables to database (migrations run automatically)
2. Configure platform credentials in environment
3. Create sync configurations via API or UI
4. Enable webhooks in external platforms
5. Monitor initial sync operations

## Future Enhancements

- Additional platforms (Azure DevOps, Linear, ClickUp)
- Advanced field mapping customization
- Scheduled sync windows
- Sync templates for common configurations
- Conflict resolution rules engine
