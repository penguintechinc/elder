"""Thin synchronous and asynchronous HTTP clients for the Elder API."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Iterator, Mapping
from typing import Any

import httpx

from elder_sdk.auth import (
    ApiKeyAuth,
    AuthConfig,
    BearerAuth,
    async_refresh_access_token,
    refresh_access_token,
)
from elder_sdk.catalog import ApiCatalog, AsyncServiceProxy, ServiceProxy
from elder_sdk.exceptions import (
    ElderAuthError,
    ElderError,
    ElderRateLimitError,
    ElderTransportError,
    error_from_response,
    raise_for_status,
)
from elder_sdk.resources import (
    ASYNC_RESOURCE_CLASSES,
    SYNC_RESOURCE_CLASSES,
    AccessReviewsResource,
    AIResource,
    ApiKeysResource,
    AsyncAccessReviewsResource,
    AsyncAIResource,
    AsyncApiKeysResource,
    AsyncAuditResource,
    AsyncAuthResource,
    AsyncBackupResource,
    AsyncCertificatesResource,
    AsyncCostsResource,
    AsyncDataStoresResource,
    AsyncDependenciesResource,
    AsyncDiagramsResource,
    AsyncDiscoveryResource,
    AsyncDocumentsResource,
    AsyncEntitiesResource,
    AsyncFlowsResource,
    AsyncGeoResource,
    AsyncGoogleWorkspaceResource,
    AsyncGraphResource,
    AsyncHelpdeskResource,
    AsyncIamResource,
    AsyncIdentitiesResource,
    AsyncIpamResource,
    AsyncIssuesResource,
    AsyncKeysResource,
    AsyncLabelsResource,
    AsyncLicensingResource,
    AsyncMetadataResource,
    AsyncMilestonesResource,
    AsyncModulesResource,
    AsyncNetworkingResource,
    AsyncOnCallResource,
    AsyncOrganizationsResource,
    AsyncPagesResource,
    AsyncPrivacyResource,
    AsyncProjectsResource,
    AsyncResourceBase,
    AsyncRunsResource,
    AsyncSbomResource,
    AsyncSearchResource,
    AsyncSecretsResource,
    AsyncServicesResource,
    AsyncSoftwareResource,
    AsyncSsoResource,
    AsyncStreamsResource,
    AsyncSyncResource,
    AsyncTenantsResource,
    AsyncUsersResource,
    AsyncVillageIdResource,
    AsyncVulnerabilitiesResource,
    AsyncWebhooksResource,
    AuditResource,
    AuthResource,
    BackupResource,
    CertificatesResource,
    CostsResource,
    DataStoresResource,
    DependenciesResource,
    DiagramsResource,
    DiscoveryResource,
    DocumentsResource,
    EntitiesResource,
    FlowsResource,
    GeoResource,
    GoogleWorkspaceResource,
    GraphResource,
    HelpdeskResource,
    IamResource,
    IdentitiesResource,
    IpamResource,
    IssuesResource,
    KeysResource,
    LabelsResource,
    LicensingResource,
    MetadataResource,
    MilestonesResource,
    ModulesResource,
    NetworkingResource,
    OnCallResource,
    OrganizationsResource,
    PagesResource,
    PrivacyResource,
    ProjectsResource,
    RunsResource,
    SbomResource,
    SearchResource,
    SecretsResource,
    ServicesResource,
    SoftwareResource,
    SsoResource,
    StreamsResource,
    SyncResource,
    SyncResourceBase,
    TenantsResource,
    UsersResource,
    VillageIdResource,
    VulnerabilitiesResource,
    WebhooksResource,
)

RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 502, 503, 504})
DEFAULT_MAX_BACKOFF: float = 30.0

_PREFERRED_ITEMS_KEYS: tuple[str, ...] = (
    "items",
    "Resources",
    "results",
    "data",
    "jobs",
    "history",
    "configs",
    "conflicts",
    "mappings",
    "issues",
    "entities",
    "organizations",
    "users",
    "identities",
    "tenants",
    "tickets",
    "rotations",
    "documents",
    "pages",
    "diagrams",
    "streams",
    "webhooks",
    "secrets",
    "keys",
    "certificates",
    "vulnerabilities",
    "software",
    "services",
)


def _clean_params(params: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Drop ``None`` query parameters cleanly."""
    if not params:
        return None
    cleaned = {k: v for k, v in params.items() if v is not None}
    return cleaned or None


def _normalize_request_path(path: str) -> str:
    """Ensure relative paths start with ``/``."""
    if not path or not path.strip():
        raise ValueError("Request path must not be empty")
    cleaned = path.strip()
    if cleaned.startswith(("http://", "https://")):
        return cleaned
    return cleaned if cleaned.startswith("/") else f"/{cleaned}"


def _build_headers(
    config: AuthConfig,
    default_headers: Mapping[str, str],
    headers: Mapping[str, str] | None,
) -> dict[str, str]:
    """Assemble request headers with auth injection and caller overrides."""
    merged = dict(default_headers)
    if config.auth is not None:
        merged = config.auth.apply_headers(merged)
    if headers:
        merged.update(headers)
    return merged


def _compute_backoff(
    attempt: int,
    backoff_factor: float,
    retry_after: float | None = None,
) -> float:
    """Compute bounded exponential backoff delay in seconds."""
    exp_delay = backoff_factor * (2**attempt)
    if retry_after is not None and retry_after > exp_delay:
        exp_delay = retry_after
    return min(exp_delay, DEFAULT_MAX_BACKOFF)


def _parse_response(response: httpx.Response) -> Any:
    """Validate response status and decode JSON, text, or binary payloads."""
    raise_for_status(response)
    if response.status_code == 204 or not response.content:
        return None

    content_type = response.headers.get("Content-Type", "").lower()
    if "json" in content_type:
        return response.json()

    if content_type.startswith("text/") or any(
        token in content_type for token in ("xml", "svg", "csv")
    ):
        return response.text

    try:
        return response.json()
    except ValueError:
        return response.content


def _extract_page_items(payload: Any, items_key: str | None) -> list[Any]:
    """Extract the list of items from a paginated API response."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, Mapping):
        return []
    if items_key is not None:
        raw = payload.get(items_key, [])
        return list(raw) if isinstance(raw, list) else []
    for key in _PREFERRED_ITEMS_KEYS:
        val = payload.get(key)
        if isinstance(val, list):
            return val
    for val in payload.values():
        if isinstance(val, list):
            return val
    return []


def _has_more_pages(
    payload: Any,
    items: list[Any],
    page: int,
    per_page: int,
    fetched_so_far: int,
) -> bool:
    """Determine whether another pagination request should be issued."""
    if not items:
        return False
    if isinstance(payload, Mapping):
        pagination = payload.get("pagination")
        meta = pagination if isinstance(pagination, Mapping) else payload
        pages = meta.get("pages") or meta.get("total_pages")
        if isinstance(pages, int) and not isinstance(pages, bool):
            return page < pages
        total = meta.get("total")
        if isinstance(total, int) and not isinstance(total, bool):
            return fetched_so_far < total
    return len(items) >= per_page


def _build_login_payload(
    email: str,
    password: str,
    *,
    portal: bool,
    tenant: str | None,
) -> tuple[str, dict[str, Any]]:
    """Build endpoint path and request payload for Elder login."""
    if not email or not email.strip():
        raise ElderAuthError("email is required")
    if not password:
        raise ElderAuthError("password is required")
    if portal:
        payload: dict[str, Any] = {"email": email.strip(), "password": password}
        if tenant is not None and tenant.strip():
            payload["tenant"] = tenant.strip()
        return "/api/v1/portal-auth/login", payload
    return "/api/v1/auth/login", {"username": email.strip(), "password": password}


def _build_ai_research_payload(
    query: str,
    *,
    model: str | None,
    history: list[dict[str, Any]] | None,
    max_steps: int | None,
) -> dict[str, Any]:
    """Build request JSON for ``POST /api/v1/search/ai-research``."""
    payload: dict[str, Any] = {"query": query}
    if model is not None:
        payload["model"] = model
    if history is not None:
        payload["history"] = history
    if max_steps is not None:
        payload["max_steps"] = max_steps
    return payload


class _BaseElderClient:
    """Shared configuration, auth-state updates, and catalog lookup."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        api_key: str | None = None,
        refresh_token: str | None = None,
        auth: BearerAuth | ApiKeyAuth | None = None,
        config: AuthConfig | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_factor: float = 0.25,
        verify_ssl: bool = True,
        catalog: ApiCatalog | None = None,
        default_headers: Mapping[str, str] | None = None,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if backoff_factor < 0:
            raise ValueError("backoff_factor must be >= 0")

        if config is not None:
            resolved_config = config
        else:
            resolved_config = AuthConfig.from_env(
                base_url=base_url,
                token=token,
                api_key=api_key,
                refresh_token=refresh_token,
                timeout=timeout,
                verify_ssl=verify_ssl,
            )
            if auth is not None:
                resolved_config.auth = auth

        self._config = resolved_config
        self._catalog = catalog or ApiCatalog.load_default()
        self._max_retries = max_retries
        self._backoff_factor = backoff_factor
        self._default_headers: dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": "elder-sdk/4.0.0",
        }
        if default_headers:
            self._default_headers.update(default_headers)
        self._resource_cache: dict[str, Any] = {}

    @property
    def config(self) -> AuthConfig:
        """Return the active ``AuthConfig``."""
        return self._config

    @property
    def base_url(self) -> str:
        """Return the normalized Elder API base URL."""
        return self._config.base_url

    @property
    def catalog(self) -> ApiCatalog:
        """Return the ``ApiCatalog`` powering dynamic dispatch."""
        return self._catalog

    def _apply_auth_response(self, data: Any) -> Any:
        """Update stored ``BearerAuth`` when login/refresh returns tokens."""
        if not isinstance(data, Mapping):
            return data
        access = data.get("access_token")
        refresh = data.get("refresh_token")
        if isinstance(access, str) and access.strip():
            refresh_str = (
                refresh.strip()
                if isinstance(refresh, str) and refresh.strip()
                else None
            )
            if isinstance(self._config.auth, BearerAuth):
                self._config.auth.update_tokens(access, refresh_token=refresh_str)
            else:
                self._config.auth = BearerAuth(access, refresh_token=refresh_str)
        return data

    def _resolve_refresh_token(self, refresh_token: str | None) -> str:
        """Resolve an explicit or stored refresh token."""
        if refresh_token and refresh_token.strip():
            return refresh_token.strip()
        if (
            isinstance(self._config.auth, BearerAuth)
            and self._config.auth.refresh_token
        ):
            return self._config.auth.refresh_token
        raise ElderAuthError("No refresh_token provided or stored on client")

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(config={self._config!r}, "
            f"services={len(self._catalog.services())})"
        )

    def __str__(self) -> str:
        return repr(self)


class ElderClient(_BaseElderClient):
    """Synchronous thin HTTP client for the Elder platform API."""

    # Typed attributes for major WebUI modules
    auth: AuthResource
    organizations: OrganizationsResource
    entities: EntitiesResource
    issues: IssuesResource
    documents: DocumentsResource
    pages: PagesResource
    diagrams: DiagramsResource
    streams: StreamsResource
    flows: FlowsResource
    helpdesk: HelpdeskResource
    ipam: IpamResource
    sbom: SbomResource
    vulnerabilities: VulnerabilitiesResource
    secrets: SecretsResource
    keys: KeysResource
    certificates: CertificatesResource
    discovery: DiscoveryResource
    sync: SyncResource
    iam: IamResource
    google_workspace: GoogleWorkspaceResource
    on_call: OnCallResource
    webhooks: WebhooksResource
    access_reviews: AccessReviewsResource
    search: SearchResource
    licensing: LicensingResource
    village_id: VillageIdResource
    tenants: TenantsResource
    identities: IdentitiesResource
    users: UsersResource
    audit: AuditResource
    backup: BackupResource
    privacy: PrivacyResource
    data_stores: DataStoresResource
    dependencies: DependenciesResource
    networking: NetworkingResource
    geo: GeoResource
    graph: GraphResource
    services: ServicesResource
    software: SoftwareResource
    projects: ProjectsResource
    milestones: MilestonesResource
    labels: LabelsResource
    metadata: MetadataResource
    costs: CostsResource
    ai: AIResource
    sso: SsoResource
    api_keys: ApiKeysResource
    modules: ModulesResource
    runs: RunsResource

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        api_key: str | None = None,
        refresh_token: str | None = None,
        auth: BearerAuth | ApiKeyAuth | None = None,
        config: AuthConfig | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_factor: float = 0.25,
        verify_ssl: bool = True,
        transport: httpx.BaseTransport | None = None,
        catalog: ApiCatalog | None = None,
        default_headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            token=token,
            api_key=api_key,
            refresh_token=refresh_token,
            auth=auth,
            config=config,
            timeout=timeout,
            max_retries=max_retries,
            backoff_factor=backoff_factor,
            verify_ssl=verify_ssl,
            catalog=catalog,
            default_headers=default_headers,
        )
        client_kwargs: dict[str, Any] = {
            "base_url": self._config.base_url,
            "timeout": self._config.timeout,
            "verify": self._config.verify_ssl,
        }
        if transport is not None:
            client_kwargs["transport"] = transport
        self._http = httpx.Client(**client_kwargs)

    @classmethod
    def from_env(
        cls,
        *,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_factor: float = 0.25,
        verify_ssl: bool = True,
        transport: httpx.BaseTransport | None = None,
        catalog: ApiCatalog | None = None,
    ) -> ElderClient:
        """Create an ``ElderClient`` configured from ``ELDER_*`` env vars."""
        return cls(
            config=AuthConfig.from_env(timeout=timeout, verify_ssl=verify_ssl),
            max_retries=max_retries,
            backoff_factor=backoff_factor,
            transport=transport,
            catalog=catalog,
        )

    def __enter__(self) -> ElderClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying ``httpx.Client``."""
        self._http.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        data: Any = None,
        files: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Send an HTTP request with bounded retries on 429/502/503/504."""
        norm_method = method.upper()
        norm_path = _normalize_request_path(path)
        cleaned_params = _clean_params(params)

        for attempt in range(self._max_retries + 1):
            req_headers = _build_headers(self._config, self._default_headers, headers)
            try:
                response = self._http.request(
                    norm_method,
                    norm_path,
                    params=cleaned_params,
                    json=json,
                    data=data,
                    files=files,
                    headers=req_headers,
                )
            except httpx.TransportError as exc:
                if attempt < self._max_retries:
                    delay = _compute_backoff(attempt, self._backoff_factor)
                    if delay > 0:
                        time.sleep(delay)
                    continue
                raise ElderTransportError(
                    f"Transport error calling {norm_method} {norm_path}: {exc}"
                ) from exc

            if (
                response.status_code in RETRYABLE_STATUS_CODES
                and attempt < self._max_retries
            ):
                err = error_from_response(response)
                retry_after = (
                    err.retry_after if isinstance(err, ElderRateLimitError) else None
                )
                delay = _compute_backoff(
                    attempt, self._backoff_factor, retry_after=retry_after
                )
                if delay > 0:
                    time.sleep(delay)
                continue

            return _parse_response(response)

        raise ElderError("Request retry loop exited unexpectedly")  # pragma: no cover

    def get(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Issue an authenticated GET request."""
        return self.request("GET", path, params=params, headers=headers)

    def post(
        self,
        path: str,
        *,
        json: Any = None,
        data: Any = None,
        files: Any = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Issue an authenticated POST request."""
        return self.request(
            "POST",
            path,
            params=params,
            json=json,
            data=data,
            files=files,
            headers=headers,
        )

    def put(
        self,
        path: str,
        *,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Issue an authenticated PUT request."""
        return self.request("PUT", path, params=params, json=json, headers=headers)

    def patch(
        self,
        path: str,
        *,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Issue an authenticated PATCH request."""
        return self.request("PATCH", path, params=params, json=json, headers=headers)

    def delete(
        self,
        path: str,
        *,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Issue an authenticated DELETE request."""
        return self.request("DELETE", path, params=params, json=json, headers=headers)

    def service(self, name: str) -> ServiceProxy:
        """Return a dynamic ``ServiceProxy`` for any service in the catalog."""
        return ServiceProxy(self, name, self._catalog)

    def invoke(self, service: str, operation: str, *args: Any, **kwargs: Any) -> Any:
        """Invoke a catalog ``(service, operation)`` synchronously."""
        return self.service(service).invoke(operation, *args, **kwargs)

    def paginate(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        page_param: str = "page",
        per_page_param: str = "per_page",
        start_page: int = 1,
        per_page: int = 50,
        items_key: str | None = None,
        max_pages: int | None = None,
    ) -> Iterator[Any]:
        """Yield items across paginated GET responses until exhausted."""
        page = start_page
        pages_fetched = 0
        fetched_total = 0

        while True:
            if max_pages is not None and pages_fetched >= max_pages:
                break
            req_params: dict[str, Any] = dict(params) if params else {}
            req_params[page_param] = page
            req_params[per_page_param] = per_page

            payload = self.get(path, params=req_params)
            pages_fetched += 1
            items = _extract_page_items(payload, items_key)
            fetched_total += len(items)

            yield from items

            if not _has_more_pages(payload, items, page, per_page, fetched_total):
                break
            page += 1

    def login(
        self,
        email: str,
        password: str,
        *,
        portal: bool = False,
        tenant: str | None = None,
    ) -> dict[str, Any]:
        """Authenticate and store the returned Bearer token on this client."""
        path, payload = _build_login_payload(
            email, password, portal=portal, tenant=tenant
        )
        data = self.post(path, json=payload)
        return self._apply_auth_response(data)

    def refresh_token(
        self,
        refresh_token: str | None = None,
        *,
        portal: bool = False,
    ) -> dict[str, Any]:
        """Refresh the Bearer access token and update stored credentials."""
        tok = self._resolve_refresh_token(refresh_token)
        data = refresh_access_token(self._http, tok, portal=portal)
        return self._apply_auth_response(data)

    def resolve_village_id(self, vid: str) -> dict[str, Any]:
        """Resolve a 25-char Village ID via ``GET /api/v1/id/{village_id}``."""
        return self.invoke("village_id", "resolve", vid)

    def licensing_status(self) -> dict[str, Any]:
        """Return effective license tier and entitlements."""
        return self.invoke("licensing", "status")

    def ai_research(
        self,
        query: str,
        *,
        model: str | None = None,
        history: list[dict[str, Any]] | None = None,
        max_steps: int | None = None,
    ) -> dict[str, Any]:
        """Execute an AI Researcher query via ``POST /api/v1/search/ai-research``."""
        payload = _build_ai_research_payload(
            query, model=model, history=history, max_steps=max_steps
        )
        return self.invoke("search", "run_ai_research", body=payload)

    def healthz(self) -> dict[str, Any]:
        """Check API health via ``GET /healthz``."""
        return self.invoke("status", "healthz")

    def __getattr__(self, name: str) -> SyncResourceBase | ServiceProxy:
        if name.startswith("_"):
            raise AttributeError(name)
        cached = self._resource_cache.get(name)
        if cached is not None:
            return cached

        res_cls = SYNC_RESOURCE_CLASSES.get(name)
        if res_cls is not None:
            instance = res_cls(self)
            self._resource_cache[name] = instance
            return instance

        if self._catalog.has_service(name):
            proxy = self.service(name)
            self._resource_cache[name] = proxy
            return proxy

        raise AttributeError(
            f"{self.__class__.__name__} has no resource or service '{name}'"
        )

    def __dir__(self) -> list[str]:
        return sorted(
            set(super().__dir__())
            | set(SYNC_RESOURCE_CLASSES.keys())
            | set(self._catalog.services())
        )


class AsyncElderClient(_BaseElderClient):
    """Asynchronous thin HTTP client for the Elder platform API."""

    auth: AsyncAuthResource
    organizations: AsyncOrganizationsResource
    entities: AsyncEntitiesResource
    issues: AsyncIssuesResource
    documents: AsyncDocumentsResource
    pages: AsyncPagesResource
    diagrams: AsyncDiagramsResource
    streams: AsyncStreamsResource
    flows: AsyncFlowsResource
    helpdesk: AsyncHelpdeskResource
    ipam: AsyncIpamResource
    sbom: AsyncSbomResource
    vulnerabilities: AsyncVulnerabilitiesResource
    secrets: AsyncSecretsResource
    keys: AsyncKeysResource
    certificates: AsyncCertificatesResource
    discovery: AsyncDiscoveryResource
    sync: AsyncSyncResource
    iam: AsyncIamResource
    google_workspace: AsyncGoogleWorkspaceResource
    on_call: AsyncOnCallResource
    webhooks: AsyncWebhooksResource
    access_reviews: AsyncAccessReviewsResource
    search: AsyncSearchResource
    licensing: AsyncLicensingResource
    village_id: AsyncVillageIdResource
    tenants: AsyncTenantsResource
    identities: AsyncIdentitiesResource
    users: AsyncUsersResource
    audit: AsyncAuditResource
    backup: AsyncBackupResource
    privacy: AsyncPrivacyResource
    data_stores: AsyncDataStoresResource
    dependencies: AsyncDependenciesResource
    networking: AsyncNetworkingResource
    geo: AsyncGeoResource
    graph: AsyncGraphResource
    services: AsyncServicesResource
    software: AsyncSoftwareResource
    projects: AsyncProjectsResource
    milestones: AsyncMilestonesResource
    labels: AsyncLabelsResource
    metadata: AsyncMetadataResource
    costs: AsyncCostsResource
    ai: AsyncAIResource
    sso: AsyncSsoResource
    api_keys: AsyncApiKeysResource
    modules: AsyncModulesResource
    runs: AsyncRunsResource

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        api_key: str | None = None,
        refresh_token: str | None = None,
        auth: BearerAuth | ApiKeyAuth | None = None,
        config: AuthConfig | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_factor: float = 0.25,
        verify_ssl: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
        catalog: ApiCatalog | None = None,
        default_headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            token=token,
            api_key=api_key,
            refresh_token=refresh_token,
            auth=auth,
            config=config,
            timeout=timeout,
            max_retries=max_retries,
            backoff_factor=backoff_factor,
            verify_ssl=verify_ssl,
            catalog=catalog,
            default_headers=default_headers,
        )
        client_kwargs: dict[str, Any] = {
            "base_url": self._config.base_url,
            "timeout": self._config.timeout,
            "verify": self._config.verify_ssl,
        }
        if transport is not None:
            client_kwargs["transport"] = transport
        self._http = httpx.AsyncClient(**client_kwargs)

    @classmethod
    def from_env(
        cls,
        *,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_factor: float = 0.25,
        verify_ssl: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
        catalog: ApiCatalog | None = None,
    ) -> AsyncElderClient:
        """Create an ``AsyncElderClient`` configured from ``ELDER_*`` env vars."""
        return cls(
            config=AuthConfig.from_env(timeout=timeout, verify_ssl=verify_ssl),
            max_retries=max_retries,
            backoff_factor=backoff_factor,
            transport=transport,
            catalog=catalog,
        )

    async def __aenter__(self) -> AsyncElderClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying ``httpx.AsyncClient``."""
        await self._http.aclose()

    async def close(self) -> None:
        """Alias for ``aclose()``."""
        await self.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        data: Any = None,
        files: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Send an async HTTP request with bounded retries on 429/502/503/504."""
        norm_method = method.upper()
        norm_path = _normalize_request_path(path)
        cleaned_params = _clean_params(params)

        for attempt in range(self._max_retries + 1):
            req_headers = _build_headers(self._config, self._default_headers, headers)
            try:
                response = await self._http.request(
                    norm_method,
                    norm_path,
                    params=cleaned_params,
                    json=json,
                    data=data,
                    files=files,
                    headers=req_headers,
                )
            except httpx.TransportError as exc:
                if attempt < self._max_retries:
                    delay = _compute_backoff(attempt, self._backoff_factor)
                    if delay > 0:
                        await asyncio.sleep(delay)
                    continue
                raise ElderTransportError(
                    f"Transport error calling {norm_method} {norm_path}: {exc}"
                ) from exc

            if (
                response.status_code in RETRYABLE_STATUS_CODES
                and attempt < self._max_retries
            ):
                err = error_from_response(response)
                retry_after = (
                    err.retry_after if isinstance(err, ElderRateLimitError) else None
                )
                delay = _compute_backoff(
                    attempt, self._backoff_factor, retry_after=retry_after
                )
                if delay > 0:
                    await asyncio.sleep(delay)
                continue

            return _parse_response(response)

        raise ElderError("Request retry loop exited unexpectedly")  # pragma: no cover

    async def get(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Issue an authenticated async GET request."""
        return await self.request("GET", path, params=params, headers=headers)

    async def post(
        self,
        path: str,
        *,
        json: Any = None,
        data: Any = None,
        files: Any = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Issue an authenticated async POST request."""
        return await self.request(
            "POST",
            path,
            params=params,
            json=json,
            data=data,
            files=files,
            headers=headers,
        )

    async def put(
        self,
        path: str,
        *,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Issue an authenticated async PUT request."""
        return await self.request(
            "PUT", path, params=params, json=json, headers=headers
        )

    async def patch(
        self,
        path: str,
        *,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Issue an authenticated async PATCH request."""
        return await self.request(
            "PATCH", path, params=params, json=json, headers=headers
        )

    async def delete(
        self,
        path: str,
        *,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Issue an authenticated async DELETE request."""
        return await self.request(
            "DELETE", path, params=params, json=json, headers=headers
        )

    def service(self, name: str) -> AsyncServiceProxy:
        """Return a dynamic ``AsyncServiceProxy`` for any service in the catalog."""
        return AsyncServiceProxy(self, name, self._catalog)

    async def invoke(
        self, service: str, operation: str, *args: Any, **kwargs: Any
    ) -> Any:
        """Invoke a catalog ``(service, operation)`` asynchronously."""
        return await self.service(service).invoke(operation, *args, **kwargs)

    async def paginate(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        page_param: str = "page",
        per_page_param: str = "per_page",
        start_page: int = 1,
        per_page: int = 50,
        items_key: str | None = None,
        max_pages: int | None = None,
    ) -> AsyncIterator[Any]:
        """Asynchronously yield items across paginated GET responses."""
        page = start_page
        pages_fetched = 0
        fetched_total = 0

        while True:
            if max_pages is not None and pages_fetched >= max_pages:
                break
            req_params: dict[str, Any] = dict(params) if params else {}
            req_params[page_param] = page
            req_params[per_page_param] = per_page

            payload = await self.get(path, params=req_params)
            pages_fetched += 1
            items = _extract_page_items(payload, items_key)
            fetched_total += len(items)

            for item in items:
                yield item

            if not _has_more_pages(payload, items, page, per_page, fetched_total):
                break
            page += 1

    async def login(
        self,
        email: str,
        password: str,
        *,
        portal: bool = False,
        tenant: str | None = None,
    ) -> dict[str, Any]:
        """Asynchronously authenticate and store returned Bearer tokens."""
        path, payload = _build_login_payload(
            email, password, portal=portal, tenant=tenant
        )
        data = await self.post(path, json=payload)
        return self._apply_auth_response(data)

    async def refresh_token(
        self,
        refresh_token: str | None = None,
        *,
        portal: bool = False,
    ) -> dict[str, Any]:
        """Asynchronously refresh the Bearer access token."""
        tok = self._resolve_refresh_token(refresh_token)
        data = await async_refresh_access_token(self._http, tok, portal=portal)
        return self._apply_auth_response(data)

    async def resolve_village_id(self, vid: str) -> dict[str, Any]:
        """Resolve a 25-char Village ID via ``GET /api/v1/id/{village_id}``."""
        return await self.invoke("village_id", "resolve", vid)

    async def licensing_status(self) -> dict[str, Any]:
        """Return effective license tier and entitlements."""
        return await self.invoke("licensing", "status")

    async def ai_research(
        self,
        query: str,
        *,
        model: str | None = None,
        history: list[dict[str, Any]] | None = None,
        max_steps: int | None = None,
    ) -> dict[str, Any]:
        """Execute an AI Researcher query via ``POST /api/v1/search/ai-research``."""
        payload = _build_ai_research_payload(
            query, model=model, history=history, max_steps=max_steps
        )
        return await self.invoke("search", "run_ai_research", body=payload)

    async def healthz(self) -> dict[str, Any]:
        """Check API health via ``GET /healthz``."""
        return await self.invoke("status", "healthz")

    def __getattr__(self, name: str) -> AsyncResourceBase | AsyncServiceProxy:
        if name.startswith("_"):
            raise AttributeError(name)
        cached = self._resource_cache.get(name)
        if cached is not None:
            return cached

        res_cls = ASYNC_RESOURCE_CLASSES.get(name)
        if res_cls is not None:
            instance = res_cls(self)
            self._resource_cache[name] = instance
            return instance

        if self._catalog.has_service(name):
            proxy = self.service(name)
            self._resource_cache[name] = proxy
            return proxy

        raise AttributeError(
            f"{self.__class__.__name__} has no resource or service '{name}'"
        )

    def __dir__(self) -> list[str]:
        return sorted(
            set(super().__dir__())
            | set(ASYNC_RESOURCE_CLASSES.keys())
            | set(self._catalog.services())
        )
