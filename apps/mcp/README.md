# Elder MCP Server

An MCP (Model Context Protocol) server that allows AI agents (Claude, etc.) to query and manage Elder's entity, relationship, and resource data via authenticated REST API calls.

## Architecture

- **Transport**: stdio (standard MCP pattern for local AI agent integration)
- **Auth**: OAuth2 Bearer tokens (JWT) with 24-hour max TTL
- **Proxy Pattern**: All tool calls make authenticated HTTPX requests to the Elder REST API
- **Permission Enforcement**: Handled at the API layer; MCP server proxies the Bearer token

## Environment Variables

**Required:**
- `ELDER_API_URL`: Base URL of Elder API (e.g., `http://localhost:8000`)

**Authentication (choose one):**
- `ELDER_API_TOKEN`: Pre-issued Bearer token (recommended for service accounts)
- `ELDER_USERNAME` + `ELDER_PASSWORD`: Credentials for login (uses `/api/v1/auth/login` to obtain token)

**Examples:**

```bash
# Using pre-issued token
export ELDER_API_URL=http://localhost:8000
export ELDER_API_TOKEN=eyJhbGc...

# Using credentials (server logs in on startup)
export ELDER_API_URL=http://localhost:8000
export ELDER_USERNAME=user@example.com
export ELDER_PASSWORD=password123
```

## Tools

### Entity Tools

- **`search_entities`**: Find entities by name, type, and organization
- **`get_entity`**: Fetch full entity details including metadata
- **`update_entity`**: Update entity name, description, or metadata (partial)

### Relationship Tools

- **`get_entity_relationships`**: List all inbound/outbound relationships for an entity
- **`search_relationships`**: Find relationships by source, target, and type
- **`create_relationship`**: Create a new relationship between entities
- **`delete_relationship`**: Remove a relationship by ID

### Resource Query Tools (Read-Only)

- **`list_organizations`**: List all organizations with optional name filter
- **`get_organization`**: Fetch organization details by ID
- **`list_identities`**: List users and service accounts with filtering
- **`search_services`**: Find services by name and organization

## Error Handling

All tools return JSON-formatted responses. Errors include:

- **Session Expired**: "Elder session expired. Restart the MCP server to re-authenticate."
- **Permission Denied**: API returns 403
- **Not Found**: API returns 404
- **Validation Errors**: Tool parameter validation failures

## Security

- **Non-Root Container**: Runs as `appuser` (uid 1000)
- **Rootless Filesystem**: Read-only root filesystem compatible with Kubernetes security contexts
- **Token TTL**: Hard maximum 24 hours from login time; tool calls fail gracefully on expiry
- **No Secrets Hardcoded**: All credentials from environment variables only

## Running the Server

### Locally (development)

```bash
cd /Users/penguinz/code/elder/apps/mcp
pip install -r requirements.txt
export ELDER_API_URL=http://localhost:8000
export ELDER_API_TOKEN=<token>
python3 -m apps.mcp.server
```

### In Docker

```bash
docker build -t elder-mcp:latest apps/mcp/
docker run --rm \
  -e ELDER_API_URL=http://host.docker.internal:8000 \
  -e ELDER_API_TOKEN=<token> \
  elder-mcp:latest
```

### With Claude AI

Configure in Claude Code or other AI tools to use this MCP server via stdio transport. The server will expose all tools to the AI agent with full authentication context.

## File Structure

```
apps/mcp/
├── requirements.in            # Direct dependencies
├── requirements.txt           # Pinned with hashes (pip-compile output)
├── Dockerfile                 # Multi-stage, rootless, bookworm-based
├── auth.py                    # JWT token management, session lifecycle
├── client.py                  # Async HTTPX client wrapper
├── server.py                  # MCP server definition and tools
└── tools/
    ├── __init__.py
    ├── entities.py            # Entity CRUD operations
    ├── relationships.py       # Relationship CRUD operations
    └── resources.py           # Resource queries (read-only)
```

## Design Patterns

### Session Management

- `ElderSession`: Holds token and expiry time; validates before each request
- Token expiry extracted from JWT `exp` claim, capped at 24h from login
- Tools fail with clear error message when session expires

### Client Pattern

- `ElderClient`: Thin async HTTPX wrapper
- All methods check `session.is_valid()` before making requests
- HTTP error codes mapped to `ValueError` (4xx) or `httpx.HTTPError` (5xx)

### Tool Return Format

- All tools return JSON-formatted strings (MCP requirement)
- Success: Full response object from Elder API as pretty-printed JSON
- Error: `{"error": "message"}` object

## Testing

To verify the server can authenticate and make API calls:

```bash
export ELDER_API_URL=http://localhost:8000
export ELDER_API_TOKEN=<valid-token>
python3 -c "
import asyncio
from apps.mcp.auth import ElderSession

async def test():
    session = await ElderSession.from_credentials(
        'http://localhost:8000', 'user@example.com', 'password'
    )
    print(f'Token valid: {session.is_valid()}')
    print(f'Expires at: {session.expires_at}')

asyncio.run(test())
"
```
