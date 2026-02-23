# Elder Connector Service

The Elder Connector Service synchronizes data from external sources (AWS, GCP, Google Workspace, LDAP/LDAPS, Okta, Authentik) into Elder.

## Documentation

Complete documentation is available in the `docs/connector/` directory:

- **[Quick Start Guide](../../docs/connector/QUICKSTART.md)** - Get started in 5 minutes
- **[Full Documentation](../../docs/connector/README.md)** - Complete reference and usage guide
- **[Implementation Details](../../docs/connector/IMPLEMENTATION.md)** - Architecture and design

## Quick Usage

```bash
# 1. Configure environment variables in .env
AWS_ENABLED=true
AWS_ACCESS_KEY_ID=...

# 2. Start the connector
docker-compose up -d connector

# 3. Check health
curl http://localhost:8000/healthz
```

## Configuration

See [Configuration Template](../../docs/connector/README.md#configuration) or `.env.example` in the project root.

## Testing

Test connectivity before running:

```bash
docker-compose exec connector python3 /app/apps/worker/test_connectivity.py
```

## Project Structure

```
apps/worker/
├── connectors/          # Connector implementations
│   ├── aws_connector.py
│   ├── gcp_connector.py
│   ├── google_workspace_connector.py
│   ├── ldap_connector.py
│   ├── okta_connector.py          # Enterprise
│   ├── authentik_connector.py     # Enterprise
│   └── group_operations.py        # Write-back support
├── config/             # Configuration management
├── utils/              # Utilities (API client, logger)
├── main.py             # Service orchestrator
├── Dockerfile          # Container definition
└── requirements.txt    # Python dependencies
```

For complete documentation, see [docs/connector/](../../docs/connector/).
