# elder-sdk

Official Python 3.11+ SDK for the [Elder](https://github.com/penguintechinc/elder) platform API.

`elder-sdk` provides both synchronous (`ElderClient`) and asynchronous (`AsyncElderClient`) HTTP clients backed by `httpx` and the canonical Elder API catalog (`api_catalog.json`), giving full coverage of all 69 Elder API services and 702 operations without duplicating server business logic.

## Installation

```bash
pip install -e ./sdk/python
```

With development/test dependencies:

```bash
pip install -e "./sdk/python[dev]"
```

## Configuration & Authentication

Configure via environment variables or explicit constructor arguments:

| Environment Variable | Description | Default |
|---|---|---|
| `ELDER_API_URL` | Base URL of the Elder API server | `http://localhost:5000` |
| `ELDER_TOKEN` | Bearer JWT access token | unset |
| `ELDER_API_KEY` | Elder API key (`X-API-Key` header) | unset |

Tokens and API keys are strictly masked in `repr()` / `str()` outputs.

```python
from elder_sdk import ElderClient

# Reads ELDER_API_URL, ELDER_TOKEN, ELDER_API_KEY automatically
with ElderClient.from_env() as client:
    print(client.healthz())

# Or pass credentials explicitly / log in
with ElderClient(base_url="https://elder.example.com") as client:
    client.login("admin@example.com", "your-password")
    me = client.auth.me()
```

Using an API key:

```python
from elder_sdk import ElderClient

with ElderClient(base_url="https://elder.example.com", api_key="elder_...") as client:
    orgs = client.organizations.list()
```

## Synchronous Resource Examples

```python
from elder_sdk import ElderClient

with ElderClient(base_url="https://elder.example.com", token="jwt-token") as client:
    # CRUD & pagination on typed resource facades
    orgs = client.organizations.list(page=1, per_page=25)
    entity = client.entities.create(
        name="prod-api-1",
        entity_type="compute",
        organization_id=1,
    )
    for issue in client.issues.iter_all(status="open", per_page=50):
        print(issue["id"], issue["title"])

    # Village ID lookup & Licensing
    resolved = client.resolve_village_id("00000001-0000000000000001")
    license_info = client.licensing_status()

    # AI Researcher
    answer = client.ai_research("Which production databases lack backups?")

    # Dynamic catalog invocation for any of the 69 services / 702 operations
    configs = client.service("sync").invoke("list_sync_configs")
    diagram_cols = client.diagram_collections.list()
```

## Asynchronous Usage

```python
import asyncio
from elder_sdk import AsyncElderClient


async def main() -> None:
    async with AsyncElderClient(
        base_url="https://elder.example.com",
        token="jwt-token",
    ) as client:
        status = await client.licensing_status()
        entities = await client.entities.list(entity_type="compute")

        async for item in client.issues.iter_all(status="open"):
            print(item["id"], item["title"])


asyncio.run(main())
```

## Error Handling & Retries

- Retries use bounded exponential backoff strictly on transient network/transport errors and HTTP `429`, `502`, `503`, `504`.
- Client errors (`400`, `401`, `403`, `404`, `422`) fail immediately without retrying.
- Typed exception hierarchy:
  - `ElderError` (base)
  - `ElderAuthError` (`401`, `403`)
  - `ElderNotFoundError` (`404`)
  - `ElderValidationError` (`400`, `422`)
  - `ElderRateLimitError` (`429`, includes `.retry_after`)
  - `ElderServerError` (`5xx`)
  - `ElderTransportError` (network / timeout failures)
  - `ElderCatalogError` (invalid service, operation, or path parameter)
