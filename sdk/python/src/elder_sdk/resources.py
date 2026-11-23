"""Ergonomic typed resource facades (sync & async) backed by the Elder API catalog."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Mapping
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import quote

from elder_sdk.catalog import AsyncServiceProxy, ServiceProxy

if TYPE_CHECKING:
    from elder_sdk.client import AsyncElderClient, ElderClient


class _CatalogMethod:
    """Descriptor binding a catalog operation name onto a resource facade."""

    def __init__(self, operation: str, doc: str = "") -> None:
        self.operation = operation
        self.__doc__ = doc

    def __get__(self, instance: Any, owner: Any = None) -> Any:
        if instance is None:
            return self
        fn = getattr(instance._proxy, self.operation)
        if self.__doc__ and not getattr(fn, "__doc__", None):
            fn.__doc__ = self.__doc__
        return fn


class _ResourceBase:
    """Shared base functionality for sync and async resource facades."""

    SERVICE_NAME: ClassVar[str]

    def __init__(self, client: Any, service_name: str | None = None) -> None:
        self._client = client
        self._service = service_name or getattr(self, "SERVICE_NAME", "")
        if not self._service:
            raise ValueError(f"{self.__class__.__name__} must define SERVICE_NAME")
        self._proxy: Any = self._build_proxy(client, self._service)

    @staticmethod
    def _build_proxy(client: Any, service_name: str) -> Any:
        raise NotImplementedError

    @property
    def service_name(self) -> str:
        """Return the catalog service name for this resource."""
        return self._service

    def operations(self) -> list[str]:
        """Return sorted catalog operation names for this resource."""
        return self._proxy.operations() if self._proxy is not None else []

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or self._proxy is None:
            raise AttributeError(name)
        return getattr(self._proxy, name)

    def __dir__(self) -> list[str]:
        extra = set(self._proxy.operations()) if self._proxy is not None else set()
        return sorted(set(super().__dir__()) | extra)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(service={self._service!r})"


class SyncResourceBase(_ResourceBase):
    """Base class for synchronous Elder API resource facades."""

    _client: ElderClient
    _proxy: ServiceProxy

    @staticmethod
    def _build_proxy(client: ElderClient, service_name: str) -> ServiceProxy:
        return ServiceProxy(client, service_name)

    def iter_operation(
        self,
        operation: str = "list",
        *,
        params: Mapping[str, Any] | None = None,
        per_page: int = 50,
        items_key: str | None = None,
        max_pages: int | None = None,
        **filters: Any,
    ) -> Iterator[Any]:
        """Iterate across all pages of a paginated GET catalog operation."""
        op = self._client.catalog.get(self._service, operation)
        merged: dict[str, Any] = dict(params) if params else {}
        merged.update(filters)
        return self._client.paginate(
            op.path,
            params=merged or None,
            per_page=per_page,
            items_key=items_key,
            max_pages=max_pages,
        )


class AsyncResourceBase(_ResourceBase):
    """Base class for asynchronous Elder API resource facades."""

    _client: AsyncElderClient
    _proxy: AsyncServiceProxy

    @staticmethod
    def _build_proxy(client: AsyncElderClient, service_name: str) -> AsyncServiceProxy:
        return AsyncServiceProxy(client, service_name)

    async def iter_operation(
        self,
        operation: str = "list",
        *,
        params: Mapping[str, Any] | None = None,
        per_page: int = 50,
        items_key: str | None = None,
        max_pages: int | None = None,
        **filters: Any,
    ) -> AsyncIterator[Any]:
        """Asynchronously iterate across all pages of a paginated GET operation."""
        op = self._client.catalog.get(self._service, operation)
        merged: dict[str, Any] = dict(params) if params else {}
        merged.update(filters)
        async for item in self._client.paginate(
            op.path,
            params=merged or None,
            per_page=per_page,
            items_key=items_key,
            max_pages=max_pages,
        ):
            yield item


class _CrudMixin:
    """Standard CRUD catalog bindings shared by CRUD resources."""

    list = _CatalogMethod("list", "List resources.")
    get = _CatalogMethod("get", "Fetch a single resource.")
    create = _CatalogMethod("create", "Create a new resource.")
    update = _CatalogMethod("update", "Update an existing resource.")
    delete = _CatalogMethod("delete", "Delete an existing resource.")


class SyncCrudResource(SyncResourceBase, _CrudMixin):
    """Synchronous CRUD resource facade with pagination helper."""

    def iter_all(
        self,
        *,
        params: Mapping[str, Any] | None = None,
        per_page: int = 50,
        items_key: str | None = None,
        max_pages: int | None = None,
        **filters: Any,
    ) -> Iterator[Any]:
        """Iterate across all pages returned by ``list``."""
        return self.iter_operation(
            "list",
            params=params,
            per_page=per_page,
            items_key=items_key,
            max_pages=max_pages,
            **filters,
        )


class AsyncCrudResource(AsyncResourceBase, _CrudMixin):
    """Asynchronous CRUD resource facade with async pagination helper."""

    async def iter_all(
        self,
        *,
        params: Mapping[str, Any] | None = None,
        per_page: int = 50,
        items_key: str | None = None,
        max_pages: int | None = None,
        **filters: Any,
    ) -> AsyncIterator[Any]:
        """Asynchronously iterate across all pages returned by ``list``."""
        async for item in self.iter_operation(
            "list",
            params=params,
            per_page=per_page,
            items_key=items_key,
            max_pages=max_pages,
            **filters,
        ):
            yield item


# ---------------------------------------------------------------------------
# Mixins defining canonical operations for each WebUI module
# ---------------------------------------------------------------------------


class _AuthMixin:
    SERVICE_NAME = "auth"
    register = _CatalogMethod("register")
    logout = _CatalogMethod("logout")
    me = _CatalogMethod("get_current_user_info")
    change_password = _CatalogMethod("change_password")
    guest_enabled = _CatalogMethod("guest_enabled")
    captcha_challenge = _CatalogMethod("captcha_challenge")


class AuthResource(SyncResourceBase, _AuthMixin):
    """Synchronous authentication resource."""

    def login(
        self,
        email: str | None = None,
        password: str | None = None,
        *,
        username: str | None = None,
        portal: bool = False,
        tenant: str | None = None,
    ) -> dict[str, Any]:
        """Authenticate with email/username and password and store tokens."""
        resolved_email = email if email is not None else (username or "")
        return self._client.login(
            resolved_email, password or "", portal=portal, tenant=tenant
        )

    def refresh_token(
        self,
        refresh_token: str | None = None,
        *,
        portal: bool = False,
    ) -> dict[str, Any]:
        """Refresh the active Bearer access token."""
        return self._client.refresh_token(refresh_token, portal=portal)


class AsyncAuthResource(AsyncResourceBase, _AuthMixin):
    """Asynchronous authentication resource."""

    async def login(
        self,
        email: str | None = None,
        password: str | None = None,
        *,
        username: str | None = None,
        portal: bool = False,
        tenant: str | None = None,
    ) -> dict[str, Any]:
        """Asynchronously authenticate with email/username and password."""
        resolved_email = email if email is not None else (username or "")
        return await self._client.login(
            resolved_email, password or "", portal=portal, tenant=tenant
        )

    async def refresh_token(
        self,
        refresh_token: str | None = None,
        *,
        portal: bool = False,
    ) -> dict[str, Any]:
        """Asynchronously refresh the active Bearer access token."""
        return await self._client.refresh_token(refresh_token, portal=portal)


class _OrganizationsMixin:
    SERVICE_NAME = "organizations"
    patch = _CatalogMethod("patch")
    children = _CatalogMethod("get_organization_children")
    graph = _CatalogMethod("get_organization_graph")
    tree_stats = _CatalogMethod("get_organization_tree_stats")


class OrganizationsResource(SyncCrudResource, _OrganizationsMixin):
    """Synchronous organizations resource."""


class AsyncOrganizationsResource(AsyncCrudResource, _OrganizationsMixin):
    """Asynchronous organizations resource."""


class _EntitiesMixin:
    SERVICE_NAME = "entities"
    patch = _CatalogMethod("patch")
    update_attributes = _CatalogMethod("update_entity_attributes")
    dependencies = _CatalogMethod("get_entity_dependencies")


class EntitiesResource(SyncCrudResource, _EntitiesMixin):
    """Synchronous entities resource."""


class AsyncEntitiesResource(AsyncCrudResource, _EntitiesMixin):
    """Asynchronous entities resource."""


class _IssuesMixin:
    SERVICE_NAME = "issues"
    list_comments = _CatalogMethod("list_issue_comments")
    create_comment = _CatalogMethod("create_issue_comment")
    update_comment = _CatalogMethod("update_issue_comment")
    delete_comment = _CatalogMethod("delete_issue_comment")
    list_labels = _CatalogMethod("list_issue_labels")
    create_label = _CatalogMethod("create_issue_label")
    list_issue_labels = _CatalogMethod("list_issue_labels_for_issue")
    add_label = _CatalogMethod("add_issue_label")
    remove_label = _CatalogMethod("remove_issue_label")
    list_links = _CatalogMethod("list_issue_entity_links")
    create_link = _CatalogMethod("create_issue_entity_link")
    delete_link = _CatalogMethod("delete_issue_entity_link")
    link_project = _CatalogMethod("link_issue_to_project")
    unlink_project = _CatalogMethod("unlink_issue_from_project")
    link_milestone = _CatalogMethod("link_issue_to_milestone")
    unlink_milestone = _CatalogMethod("unlink_issue_from_milestone")
    list_resources = _CatalogMethod("list_issue_resource_links")
    create_resource = _CatalogMethod("create_issue_resource_link")
    delete_resource = _CatalogMethod("delete_issue_resource_link")


class IssuesResource(SyncCrudResource, _IssuesMixin):
    """Synchronous issues resource."""


class AsyncIssuesResource(AsyncCrudResource, _IssuesMixin):
    """Asynchronous issues resource."""


class _DocumentsMixin:
    SERVICE_NAME = "documents"
    publish = _CatalogMethod("publish_document")
    list_public = _CatalogMethod("list_public_documents")
    get_public = _CatalogMethod("get_public_document")
    list_versions = _CatalogMethod("list_versions")
    get_version = _CatalogMethod("get_version")
    restore_version = _CatalogMethod("restore_version")


class DocumentsResource(SyncCrudResource, _DocumentsMixin):
    """Synchronous documents resource."""


class AsyncDocumentsResource(AsyncCrudResource, _DocumentsMixin):
    """Asynchronous documents resource."""


class _PagesMixin:
    SERVICE_NAME = "pages"
    backlinks = _CatalogMethod("get_backlinks")
    attach_collection = _CatalogMethod("attach_collection")
    detach_collection = _CatalogMethod("detach_collection")


class PagesResource(SyncCrudResource, _PagesMixin):
    """Synchronous pages resource."""


class AsyncPagesResource(AsyncCrudResource, _PagesMixin):
    """Asynchronous pages resource."""


class _DiagramsMixin:
    SERVICE_NAME = "diagrams"
    export_json = _CatalogMethod("export_json")
    export_svg = _CatalogMethod("export_svg")
    export_raster = _CatalogMethod("export_raster")
    list_versions = _CatalogMethod("list_versions")
    save_version = _CatalogMethod("save_version")
    get_version = _CatalogMethod("get_version")
    restore_version = _CatalogMethod("restore_version")
    list_shares = _CatalogMethod("list_shares")
    create_share = _CatalogMethod("create_share")
    revoke_share = _CatalogMethod("revoke_share")
    get_shared = _CatalogMethod("get_shared_diagram")
    list_comments = _CatalogMethod("list_comments")
    create_comment = _CatalogMethod("create_comment")
    update_comment = _CatalogMethod("update_comment")
    delete_comment = _CatalogMethod("delete_comment")
    add_reply = _CatalogMethod("add_reply")
    issue_collab_ticket = _CatalogMethod("issue_collab_ticket")


class DiagramsResource(SyncCrudResource, _DiagramsMixin):
    """Synchronous diagrams resource."""


class AsyncDiagramsResource(AsyncCrudResource, _DiagramsMixin):
    """Asynchronous diagrams resource."""


class _StreamsMixin:
    SERVICE_NAME = "streams"
    patch = _CatalogMethod("patch")
    execute = _CatalogMethod("execute_stream")
    validate = _CatalogMethod("validate_stream")
    duplicate = _CatalogMethod("duplicate_stream")
    node_catalog = _CatalogMethod("get_stream_node_catalog")
    list_executions = _CatalogMethod("list_executions")
    list_all_executions = _CatalogMethod("list_all_executions")
    get_execution = _CatalogMethod("get_execution")
    acquire_lock = _CatalogMethod("acquire_lock")
    release_lock = _CatalogMethod("release_lock")
    get_lock_status = _CatalogMethod("get_lock_status")
    my_approvals = _CatalogMethod("get_my_approvals")
    approve_execution = _CatalogMethod("approve_execution")
    reject_execution = _CatalogMethod("reject_execution")
    approval_status = _CatalogMethod("get_approval_status")
    list_approval_gates = _CatalogMethod("list_approval_gates")
    create_approval_gate = _CatalogMethod("create_approval_gate")
    list_webhooks = _CatalogMethod("list_webhooks")
    create_webhook = _CatalogMethod("create_webhook")
    delete_webhook = _CatalogMethod("delete_webhook")


class StreamsResource(SyncCrudResource, _StreamsMixin):
    """Synchronous streams resource."""


class AsyncStreamsResource(AsyncCrudResource, _StreamsMixin):
    """Asynchronous streams resource."""


class _FlowsMixin:
    SERVICE_NAME = "flows"
    enable = _CatalogMethod("enable_pipeline")
    disable = _CatalogMethod("disable_pipeline")
    duplicate = _CatalogMethod("duplicate_pipeline")
    list_stages = _CatalogMethod("list_stages")
    create_stage = _CatalogMethod("create_stage")
    get_stage = _CatalogMethod("get_stage")
    update_stage = _CatalogMethod("update_stage")
    delete_stage = _CatalogMethod("delete_stage")
    list_credentials = _CatalogMethod("list_credentials")
    create_credential = _CatalogMethod("create_credential")
    get_credential = _CatalogMethod("get_credential")
    update_credential = _CatalogMethod("update_credential")
    delete_credential = _CatalogMethod("delete_credential")
    list_promotions = _CatalogMethod("list_promotions")
    get_promotion = _CatalogMethod("get_promotion")
    approve_promotion = _CatalogMethod("approve_promotion")
    reject_promotion = _CatalogMethod("reject_promotion")
    execute_promotion = _CatalogMethod("execute_promotion")
    trigger_webhook = _CatalogMethod("trigger_webhook")


class FlowsResource(SyncCrudResource, _FlowsMixin):
    """Synchronous flows resource."""


class AsyncFlowsResource(AsyncCrudResource, _FlowsMixin):
    """Asynchronous flows resource."""


class _HelpdeskMixin:
    SERVICE_NAME = "helpdesk"
    list_tickets = _CatalogMethod("list_tickets")
    create_ticket = _CatalogMethod("create_ticket")
    get_ticket = _CatalogMethod("get_ticket")
    update_ticket = _CatalogMethod("update_ticket")
    delete_ticket = _CatalogMethod("delete_ticket")
    assign_ticket = _CatalogMethod("assign_ticket")
    merge_tickets = _CatalogMethod("merge_tickets")
    list_messages = _CatalogMethod("list_messages")
    add_message = _CatalogMethod("add_message")
    dashboard_stats = _CatalogMethod("dashboard_stats")
    list_teams = _CatalogMethod("list_teams")
    create_team = _CatalogMethod("create_team")
    get_team = _CatalogMethod("get_team")
    update_team = _CatalogMethod("update_team")
    add_team_member = _CatalogMethod("add_team_member")
    remove_team_member = _CatalogMethod("remove_team_member")
    list_sla_policies = _CatalogMethod("list_sla_policies")
    create_sla_policy = _CatalogMethod("create_sla_policy")
    update_sla_policy = _CatalogMethod("update_sla_policy")
    delete_sla_policy = _CatalogMethod("delete_sla_policy")
    list_canned_responses = _CatalogMethod("list_canned_responses")
    create_canned_response = _CatalogMethod("create_canned_response")
    update_canned_response = _CatalogMethod("update_canned_response")
    delete_canned_response = _CatalogMethod("delete_canned_response")


class HelpdeskResource(SyncResourceBase, _HelpdeskMixin):
    """Synchronous helpdesk resource."""


class AsyncHelpdeskResource(AsyncResourceBase, _HelpdeskMixin):
    """Asynchronous helpdesk resource."""


class _IpamMixin:
    SERVICE_NAME = "ipam"
    list_prefixes = _CatalogMethod("list_prefixes")
    create_prefix = _CatalogMethod("create_prefix")
    get_prefix = _CatalogMethod("get_prefix")
    get_prefix_tree = _CatalogMethod("get_prefix_tree")
    update_prefix = _CatalogMethod("update_prefix")
    delete_prefix = _CatalogMethod("delete_prefix")
    list_addresses = _CatalogMethod("list_addresses")
    create_address = _CatalogMethod("create_address")
    get_address = _CatalogMethod("get_address")
    update_address = _CatalogMethod("update_address")
    delete_address = _CatalogMethod("delete_address")
    list_vlans = _CatalogMethod("list_vlans")
    create_vlan = _CatalogMethod("create_vlan")
    get_vlan = _CatalogMethod("get_vlan")
    update_vlan = _CatalogMethod("update_vlan")
    delete_vlan = _CatalogMethod("delete_vlan")


class IpamResource(SyncResourceBase, _IpamMixin):
    """Synchronous IPAM resource."""


class AsyncIpamResource(AsyncResourceBase, _IpamMixin):
    """Asynchronous IPAM resource."""


class _SbomMixin:
    SERVICE_NAME = "sbom"
    list_components = _CatalogMethod("list_components")
    create_component = _CatalogMethod("create_component")
    get_component = _CatalogMethod("get_component")
    update_component = _CatalogMethod("update_component")
    delete_component = _CatalogMethod("delete_component")
    get_component_vulnerabilities = _CatalogMethod("get_component_vulnerabilities")
    list_scans = _CatalogMethod("list_scans")
    create_scan = _CatalogMethod("create_scan")
    get_scan = _CatalogMethod("get_scan")
    delete_scan = _CatalogMethod("delete_scan")
    start_scan = _CatalogMethod("start_scan")
    submit_results = _CatalogMethod("submit_results")
    upload_sbom = _CatalogMethod("upload_sbom")
    get_pending_scans = _CatalogMethod("get_pending_scans")
    list_schedules = _CatalogMethod("list_schedules")
    create_schedule = _CatalogMethod("create_schedule")
    get_schedule = _CatalogMethod("get_schedule")
    update_schedule = _CatalogMethod("update_schedule")
    delete_schedule = _CatalogMethod("delete_schedule")
    get_due_schedules = _CatalogMethod("get_due_schedules")


class SbomResource(SyncResourceBase, _SbomMixin):
    """Synchronous SBOM resource."""


class AsyncSbomResource(AsyncResourceBase, _SbomMixin):
    """Asynchronous SBOM resource."""


class _VulnerabilitiesMixin:
    SERVICE_NAME = "vulnerabilities"
    list = _CatalogMethod("list")
    create = _CatalogMethod("create")
    get = _CatalogMethod("get")
    dashboard = _CatalogMethod("get_dashboard")
    sync = _CatalogMethod("sync_vulnerabilities")
    trigger_nvd_sync = _CatalogMethod("trigger_nvd_sync")
    nvd_sync_status = _CatalogMethod("get_nvd_sync_status")
    assign = _CatalogMethod("assign_vulnerability")
    update_component = _CatalogMethod("update_component_vulnerability")


class VulnerabilitiesResource(SyncResourceBase, _VulnerabilitiesMixin):
    """Synchronous vulnerabilities resource."""


class AsyncVulnerabilitiesResource(AsyncResourceBase, _VulnerabilitiesMixin):
    """Asynchronous vulnerabilities resource."""


class _SecretsMixin:
    SERVICE_NAME = "secrets"
    unmask = _CatalogMethod("unmask_secret")
    sync = _CatalogMethod("sync_secret")
    access_log = _CatalogMethod("get_secret_access_log")
    list_providers = _CatalogMethod("list_secret_providers")
    create_provider = _CatalogMethod("create_secret_provider")
    get_provider = _CatalogMethod("get_secret_provider")
    update_provider = _CatalogMethod("update_secret_provider")
    delete_provider = _CatalogMethod("delete_secret_provider")
    test_provider = _CatalogMethod("test_secret_provider")
    sync_provider = _CatalogMethod("sync_secret_provider")


class SecretsResource(SyncCrudResource, _SecretsMixin):
    """Synchronous secrets resource."""


class AsyncSecretsResource(AsyncCrudResource, _SecretsMixin):
    """Asynchronous secrets resource."""


class _KeysMixin:
    SERVICE_NAME = "keys"
    list = _CatalogMethod("list")
    create = _CatalogMethod("create")
    get = _CatalogMethod("get")
    delete = _CatalogMethod("delete")
    enable = _CatalogMethod("enable_key")
    disable = _CatalogMethod("disable_key")
    rotate = _CatalogMethod("rotate_key")
    encrypt = _CatalogMethod("encrypt_data")
    decrypt = _CatalogMethod("decrypt_data")
    generate_data_key = _CatalogMethod("generate_data_key")
    sign = _CatalogMethod("sign_data")
    verify = _CatalogMethod("verify_signature")
    access_log = _CatalogMethod("get_key_access_log")
    list_providers = _CatalogMethod("list_key_providers")
    create_provider = _CatalogMethod("create_key_provider")
    get_provider = _CatalogMethod("get_key_provider")
    update_provider = _CatalogMethod("update_key_provider")
    delete_provider = _CatalogMethod("delete_key_provider")
    test_provider = _CatalogMethod("test_key_provider")


class KeysResource(SyncResourceBase, _KeysMixin):
    """Synchronous KMS keys resource."""


class AsyncKeysResource(AsyncResourceBase, _KeysMixin):
    """Asynchronous KMS keys resource."""


class CertificatesResource(SyncCrudResource):
    """Synchronous certificates resource."""

    SERVICE_NAME = "certificates"


class AsyncCertificatesResource(AsyncCrudResource):
    """Asynchronous certificates resource."""

    SERVICE_NAME = "certificates"


class _DiscoveryMixin:
    SERVICE_NAME = "discovery"
    list_jobs = _CatalogMethod("list_discovery_jobs")
    create_job = _CatalogMethod("create_discovery_job")
    get_job = _CatalogMethod("get_discovery_job")
    update_job = _CatalogMethod("update_discovery_job")
    delete_job = _CatalogMethod("delete_discovery_job")
    test_job = _CatalogMethod("test_discovery_job")
    run_job = _CatalogMethod("run_discovery_job")
    job_history = _CatalogMethod("get_discovery_job_history")
    history = _CatalogMethod("get_all_discovery_history")
    pending_jobs = _CatalogMethod("get_pending_jobs")
    start_job = _CatalogMethod("start_job")
    complete_job = _CatalogMethod("complete_job")


class DiscoveryResource(SyncResourceBase, _DiscoveryMixin):
    """Synchronous discovery resource."""


class AsyncDiscoveryResource(AsyncResourceBase, _DiscoveryMixin):
    """Asynchronous discovery resource."""


class _SyncMixin:
    SERVICE_NAME = "sync"
    list_configs = _CatalogMethod("list_sync_configs")
    create_config = _CatalogMethod("create_sync_config")
    get_config = _CatalogMethod("get_sync_config")
    update_config = _CatalogMethod("update_sync_config")
    delete_config = _CatalogMethod("delete_sync_config")
    test_config = _CatalogMethod("test_sync_config")
    trigger_config = _CatalogMethod("trigger_sync_config")
    status = _CatalogMethod("sync_status")
    history = _CatalogMethod("list_sync_history")
    mappings = _CatalogMethod("list_sync_mappings")
    list_conflicts = _CatalogMethod("list_sync_conflicts")
    resolve_conflict = _CatalogMethod("resolve_conflict")
    receive_webhook = _CatalogMethod("receive_sync_webhook")


class SyncResource(SyncResourceBase, _SyncMixin):
    """Synchronous integrations sync resource."""


class AsyncSyncResource(AsyncResourceBase, _SyncMixin):
    """Asynchronous integrations sync resource."""


class _IamMixin:
    SERVICE_NAME = "iam"
    list_providers = _CatalogMethod("list_providers")
    create_provider = _CatalogMethod("create_provider")
    get_provider = _CatalogMethod("get_provider")
    update_provider = _CatalogMethod("update_provider")
    delete_provider = _CatalogMethod("delete_provider")
    test_provider = _CatalogMethod("test_provider")
    sync_provider = _CatalogMethod("sync_provider")
    list_users = _CatalogMethod("list_users")
    create_user = _CatalogMethod("create_user")
    get_user = _CatalogMethod("get_user")
    update_user = _CatalogMethod("update_user")
    delete_user = _CatalogMethod("delete_user")
    list_roles = _CatalogMethod("list_roles")
    create_role = _CatalogMethod("create_role")
    get_role = _CatalogMethod("get_role")
    update_role = _CatalogMethod("update_role")
    delete_role = _CatalogMethod("delete_role")
    list_policies = _CatalogMethod("list_policies")
    create_policy = _CatalogMethod("create_policy")
    get_policy = _CatalogMethod("get_policy")
    delete_policy = _CatalogMethod("delete_policy")
    list_groups = _CatalogMethod("list_groups")
    create_group = _CatalogMethod("create_group")
    delete_group = _CatalogMethod("delete_group")


class IamResource(SyncResourceBase, _IamMixin):
    """Synchronous IAM resource."""


class AsyncIamResource(AsyncResourceBase, _IamMixin):
    """Asynchronous IAM resource."""


class _GoogleWorkspaceMixin:
    SERVICE_NAME = "google_workspace"
    list_providers = _CatalogMethod("list_providers")
    create_provider = _CatalogMethod("create_provider")
    get_provider = _CatalogMethod("get_provider")
    update_provider = _CatalogMethod("update_provider")
    delete_provider = _CatalogMethod("delete_provider")
    test_provider = _CatalogMethod("test_provider")
    list_users = _CatalogMethod("list_users")
    create_user = _CatalogMethod("create_user")
    get_user = _CatalogMethod("get_user")
    update_user = _CatalogMethod("update_user")
    delete_user = _CatalogMethod("delete_user")
    list_groups = _CatalogMethod("list_groups")
    create_group = _CatalogMethod("create_group")
    get_group = _CatalogMethod("get_group")
    delete_group = _CatalogMethod("delete_group")
    list_group_members = _CatalogMethod("list_group_members")
    add_group_member = _CatalogMethod("add_group_member")
    remove_group_member = _CatalogMethod("remove_group_member")


class GoogleWorkspaceResource(SyncResourceBase, _GoogleWorkspaceMixin):
    """Synchronous Google Workspace resource."""


class AsyncGoogleWorkspaceResource(AsyncResourceBase, _GoogleWorkspaceMixin):
    """Asynchronous Google Workspace resource."""


class _OnCallMixin:
    SERVICE_NAME = "on_call"
    list_rotations = _CatalogMethod("list_rotations")
    create_rotation = _CatalogMethod("create_rotation")
    get_rotation = _CatalogMethod("get_rotation")
    update_rotation = _CatalogMethod("update_rotation")
    delete_rotation = _CatalogMethod("delete_rotation")
    current_for_org = _CatalogMethod("get_current_oncall_for_org")
    current_for_service = _CatalogMethod("get_current_oncall_for_service")
    shift_history = _CatalogMethod("get_shift_history")
    list_participants = _CatalogMethod("list_participants")
    add_participant = _CatalogMethod("add_participant")
    update_participant = _CatalogMethod("update_participant")
    remove_participant = _CatalogMethod("remove_participant")
    list_overrides = _CatalogMethod("list_overrides")
    create_override = _CatalogMethod("create_override")
    update_override = _CatalogMethod("update_override")
    delete_override = _CatalogMethod("delete_override")
    list_escalations = _CatalogMethod("list_escalations")
    create_escalation = _CatalogMethod("create_escalation")
    update_escalation = _CatalogMethod("update_escalation")
    delete_escalation = _CatalogMethod("delete_escalation")
    alertmanager_webhook = _CatalogMethod("handle_alertmanager_webhook")


class OnCallResource(SyncResourceBase, _OnCallMixin):
    """Synchronous on-call rotations resource."""


class AsyncOnCallResource(AsyncResourceBase, _OnCallMixin):
    """Asynchronous on-call rotations resource."""


class _WebhooksMixin:
    SERVICE_NAME = "webhooks"
    test = _CatalogMethod("test_webhook")
    deliveries = _CatalogMethod("get_webhook_deliveries")
    redeliver = _CatalogMethod("redeliver_webhook")
    broadcast = _CatalogMethod("broadcast_event")
    list_notification_rules = _CatalogMethod("list_notification_rules")
    create_notification_rule = _CatalogMethod("create_notification_rule")
    get_notification_rule = _CatalogMethod("get_notification_rule")
    update_notification_rule = _CatalogMethod("update_notification_rule")
    delete_notification_rule = _CatalogMethod("delete_notification_rule")
    test_notification_rule = _CatalogMethod("test_notification_rule")
    list_inbound = _CatalogMethod("list_inbound_webhooks")
    create_inbound = _CatalogMethod("create_inbound_webhook")
    update_inbound = _CatalogMethod("update_inbound_webhook")
    delete_inbound = _CatalogMethod("delete_inbound_webhook")
    inbound_events = _CatalogMethod("list_inbound_webhook_events")
    inbound_scopes = _CatalogMethod("list_inbound_webhook_scopes")
    rotate_inbound_credentials = _CatalogMethod("rotate_inbound_webhook_credentials")
    receive_inbound = _CatalogMethod("receive_inbound_webhook")


class WebhooksResource(SyncCrudResource, _WebhooksMixin):
    """Synchronous webhooks resource."""


class AsyncWebhooksResource(AsyncCrudResource, _WebhooksMixin):
    """Asynchronous webhooks resource."""


class _AccessReviewsMixin:
    SERVICE_NAME = "access_reviews"
    list = _CatalogMethod("list")
    create = _CatalogMethod("create")
    get = _CatalogMethod("get")
    items = _CatalogMethod("get_review_items")
    my_reviews = _CatalogMethod("get_my_reviews")
    submit_decisions = _CatalogMethod("submit_decisions")
    complete = _CatalogMethod("complete_review")


class AccessReviewsResource(SyncResourceBase, _AccessReviewsMixin):
    """Synchronous access reviews resource."""


class AsyncAccessReviewsResource(AsyncResourceBase, _AccessReviewsMixin):
    """Asynchronous access reviews resource."""


class _SearchMixin:
    SERVICE_NAME = "search"
    query = _CatalogMethod("search")
    search = _CatalogMethod("search")
    entities = _CatalogMethod("search_entities")
    organizations = _CatalogMethod("search_organizations")
    issues = _CatalogMethod("search_issues")
    graph = _CatalogMethod("search_graph")
    suggest = _CatalogMethod("search_suggestions")
    popular = _CatalogMethod("get_popular_searches")
    list_saved = _CatalogMethod("list_saved_searches")
    create_saved = _CatalogMethod("create_saved_search")
    get_saved = _CatalogMethod("get_saved_search")
    update_saved = _CatalogMethod("update_saved_search")
    delete_saved = _CatalogMethod("delete_saved_search")
    execute_saved = _CatalogMethod("execute_saved_search")
    ai_research_config = _CatalogMethod("get_ai_research_config")


class SearchResource(SyncResourceBase, _SearchMixin):
    """Synchronous search and AI Researcher resource."""

    def ai_research(
        self,
        query: str,
        *,
        model: str | None = None,
        history: list[dict[str, Any]] | None = None,
        max_steps: int | None = None,
    ) -> dict[str, Any]:
        """Run an AI Researcher query via ``POST /api/v1/search/ai-research``."""
        return self._client.ai_research(
            query, model=model, history=history, max_steps=max_steps
        )


class AsyncSearchResource(AsyncResourceBase, _SearchMixin):
    """Asynchronous search and AI Researcher resource."""

    async def ai_research(
        self,
        query: str,
        *,
        model: str | None = None,
        history: list[dict[str, Any]] | None = None,
        max_steps: int | None = None,
    ) -> dict[str, Any]:
        """Asynchronously run an AI Researcher query."""
        return await self._client.ai_research(
            query, model=model, history=history, max_steps=max_steps
        )


class _LicensingMixin:
    SERVICE_NAME = "licensing"
    status = _CatalogMethod("status")


class LicensingResource(SyncResourceBase, _LicensingMixin):
    """Synchronous licensing status resource."""


class AsyncLicensingResource(AsyncResourceBase, _LicensingMixin):
    """Asynchronous licensing status resource."""


class _VillageIdMixin:
    SERVICE_NAME = "village_id"
    resolve = _CatalogMethod("resolve")
    resolve_legacy = _CatalogMethod("resolve_legacy")


class VillageIdResource(SyncResourceBase, _VillageIdMixin):
    """Synchronous Village ID resolver resource."""


class AsyncVillageIdResource(AsyncResourceBase, _VillageIdMixin):
    """Asynchronous Village ID resolver resource."""


class _TenantsMixin:
    SERVICE_NAME = "tenants"
    list_users = _CatalogMethod("list_tenant_users")
    update_user = _CatalogMethod("update_tenant_user")
    delete_user = _CatalogMethod("delete_tenant_user")
    stats = _CatalogMethod("get_tenant_stats")


class TenantsResource(SyncCrudResource, _TenantsMixin):
    """Synchronous tenants resource."""


class AsyncTenantsResource(AsyncCrudResource, _TenantsMixin):
    """Asynchronous tenants resource."""


class _IdentitiesMixin:
    SERVICE_NAME = "identities"
    patch = _CatalogMethod("patch")
    list_groups = _CatalogMethod("list_groups")
    create_group = _CatalogMethod("create_group")
    get_group = _CatalogMethod("get_group")
    update_group = _CatalogMethod("update_group")
    patch_group = _CatalogMethod("patch_group")
    delete_group = _CatalogMethod("delete_group")
    add_group_member = _CatalogMethod("add_group_member")
    remove_group_member = _CatalogMethod("remove_group_member")


class IdentitiesResource(SyncCrudResource, _IdentitiesMixin):
    """Synchronous identities resource."""


class AsyncIdentitiesResource(AsyncCrudResource, _IdentitiesMixin):
    """Asynchronous identities resource."""


class _UsersMixin:
    SERVICE_NAME = "users"
    list = _CatalogMethod("list")
    create = _CatalogMethod("create")
    update = _CatalogMethod("update")
    delete = _CatalogMethod("delete")


class UsersResource(SyncResourceBase, _UsersMixin):
    """Synchronous portal users resource."""


class AsyncUsersResource(AsyncResourceBase, _UsersMixin):
    """Asynchronous portal users resource."""


class _AuditMixin:
    SERVICE_NAME = "audit"
    list_retention_policies = _CatalogMethod("list_retention_policies")
    get_retention_policy = _CatalogMethod("get_retention_policy")
    create_retention_policy = _CatalogMethod("create_retention_policy")
    update_retention_policy = _CatalogMethod("update_retention_policy")
    delete_retention_policy = _CatalogMethod("delete_retention_policy")
    cleanup = _CatalogMethod("cleanup_audit_logs")


class AuditResource(SyncResourceBase, _AuditMixin):
    """Synchronous audit retention resource."""


class AsyncAuditResource(AsyncResourceBase, _AuditMixin):
    """Asynchronous audit retention resource."""


class _BackupMixin:
    SERVICE_NAME = "backup"
    list = _CatalogMethod("list")
    get = _CatalogMethod("get")
    delete = _CatalogMethod("delete")
    download = _CatalogMethod("download_backup")
    restore = _CatalogMethod("restore_backup")
    stats = _CatalogMethod("get_backup_stats")
    export_data = _CatalogMethod("export_data")
    download_export = _CatalogMethod("download_export")
    import_data = _CatalogMethod("import_data")
    list_jobs = _CatalogMethod("list_backup_jobs")
    create_job = _CatalogMethod("create_backup_job")
    get_job = _CatalogMethod("get_backup_job")
    update_job = _CatalogMethod("update_backup_job")
    delete_job = _CatalogMethod("delete_backup_job")
    run_job = _CatalogMethod("run_backup_job")


class BackupResource(SyncResourceBase, _BackupMixin):
    """Synchronous backup and export resource."""


class AsyncBackupResource(AsyncResourceBase, _BackupMixin):
    """Asynchronous backup and export resource."""


class _PrivacyMixin:
    SERVICE_NAME = "privacy"
    export_my_data = _CatalogMethod("export_my_data")
    erase_my_data = _CatalogMethod("erase_my_data")
    set_my_consent = _CatalogMethod("set_my_consent")


class PrivacyResource(SyncResourceBase, _PrivacyMixin):
    """Synchronous self-service privacy/DSAR resource."""


class AsyncPrivacyResource(AsyncResourceBase, _PrivacyMixin):
    """Asynchronous self-service privacy/DSAR resource."""


class _DataStoresMixin:
    SERVICE_NAME = "data_stores"
    list_labels = _CatalogMethod("get_data_store_labels")
    add_label = _CatalogMethod("add_data_store_label")
    remove_label = _CatalogMethod("remove_data_store_label")


class DataStoresResource(SyncCrudResource, _DataStoresMixin):
    """Synchronous data stores resource."""


class AsyncDataStoresResource(AsyncCrudResource, _DataStoresMixin):
    """Asynchronous data stores resource."""


class _DependenciesMixin:
    SERVICE_NAME = "dependencies"
    patch = _CatalogMethod("patch")
    for_resource = _CatalogMethod("get_resource_dependencies")
    create_bulk = _CatalogMethod("create_bulk_dependencies")
    delete_bulk = _CatalogMethod("delete_bulk_dependencies")


class DependenciesResource(SyncCrudResource, _DependenciesMixin):
    """Synchronous dependencies resource."""


class AsyncDependenciesResource(AsyncCrudResource, _DependenciesMixin):
    """Asynchronous dependencies resource."""


class _NetworkingMixin:
    SERVICE_NAME = "networking"
    list_networks = _CatalogMethod("list_networks")
    create_network = _CatalogMethod("create_network")
    get_network = _CatalogMethod("get_network")
    update_network = _CatalogMethod("update_network")
    patch_network = _CatalogMethod("patch_network")
    delete_network = _CatalogMethod("delete_network")
    list_connections = _CatalogMethod("list_topology_connections")
    create_connection = _CatalogMethod("create_topology_connection")
    get_connection = _CatalogMethod("get_topology_connection")
    delete_connection = _CatalogMethod("delete_topology_connection")
    list_mappings = _CatalogMethod("list_entity_mappings")
    create_mapping = _CatalogMethod("create_entity_mapping")
    get_mapping = _CatalogMethod("get_entity_mapping")
    delete_mapping = _CatalogMethod("delete_entity_mapping")
    topology_graph = _CatalogMethod("get_topology_graph")


class NetworkingResource(SyncResourceBase, _NetworkingMixin):
    """Synchronous networking and topology resource."""


class AsyncNetworkingResource(AsyncResourceBase, _NetworkingMixin):
    """Asynchronous networking and topology resource."""


class _GeoMixin:
    SERVICE_NAME = "geo"
    geocode = _CatalogMethod("geocode_location")
    list_regions = _CatalogMethod("list_regions")
    create_region = _CatalogMethod("create_region")
    update_region = _CatalogMethod("update_region")
    delete_region = _CatalogMethod("delete_region")
    list_locations = _CatalogMethod("list_locations")
    create_location = _CatalogMethod("create_location")
    update_location = _CatalogMethod("update_location")
    delete_location = _CatalogMethod("delete_location")


class GeoResource(SyncResourceBase, _GeoMixin):
    """Synchronous geographic regions and locations resource."""


class AsyncGeoResource(AsyncResourceBase, _GeoMixin):
    """Asynchronous geographic regions and locations resource."""


class _GraphMixin:
    SERVICE_NAME = "graph"
    get = _CatalogMethod("get")
    analyze = _CatalogMethod("analyze_graph")
    path = _CatalogMethod("find_path")
    map = _CatalogMethod("get_map")


class GraphResource(SyncResourceBase, _GraphMixin):
    """Synchronous relationship graph resource."""


class AsyncGraphResource(AsyncResourceBase, _GraphMixin):
    """Asynchronous relationship graph resource."""


class _ServicesMixin:
    SERVICE_NAME = "services"
    endpoints = _CatalogMethod("list_service_endpoints")
    sbom = _CatalogMethod("get_service_sbom")
    export_sbom = _CatalogMethod("export_service_sbom")
    scan_sbom = _CatalogMethod("trigger_service_sbom_scan")


class ServicesResource(SyncCrudResource, _ServicesMixin):
    """Synchronous services catalog resource."""


class AsyncServicesResource(AsyncCrudResource, _ServicesMixin):
    """Asynchronous services catalog resource."""


class _SoftwareMixin:
    SERVICE_NAME = "software"
    sbom = _CatalogMethod("get_software_sbom")
    export_sbom = _CatalogMethod("export_software_sbom")


class SoftwareResource(SyncCrudResource, _SoftwareMixin):
    """Synchronous software inventory resource."""


class AsyncSoftwareResource(AsyncCrudResource, _SoftwareMixin):
    """Asynchronous software inventory resource."""


class ProjectsResource(SyncCrudResource):
    """Synchronous projects resource."""

    SERVICE_NAME = "projects"


class AsyncProjectsResource(AsyncCrudResource):
    """Asynchronous projects resource."""

    SERVICE_NAME = "projects"


class _MilestonesMixin:
    SERVICE_NAME = "milestones"
    issues = _CatalogMethod("get_milestone_issues")


class MilestonesResource(SyncCrudResource, _MilestonesMixin):
    """Synchronous milestones resource."""


class AsyncMilestonesResource(AsyncCrudResource, _MilestonesMixin):
    """Asynchronous milestones resource."""


class LabelsResource(SyncCrudResource):
    """Synchronous labels resource."""

    SERVICE_NAME = "labels"


class AsyncLabelsResource(AsyncCrudResource):
    """Asynchronous labels resource."""

    SERVICE_NAME = "labels"


class _MetadataMixin:
    SERVICE_NAME = "metadata"
    get_entity = _CatalogMethod("get_entity_metadata")
    create_entity = _CatalogMethod("create_entity_metadata")
    update_entity = _CatalogMethod("update_entity_metadata")
    delete_entity = _CatalogMethod("delete_entity_metadata")
    get_organization = _CatalogMethod("get_organization_metadata")
    create_organization = _CatalogMethod("create_organization_metadata")
    update_organization = _CatalogMethod("update_organization_metadata")
    delete_organization = _CatalogMethod("delete_organization_metadata")


class MetadataResource(SyncResourceBase, _MetadataMixin):
    """Synchronous custom metadata resource."""


class AsyncMetadataResource(AsyncResourceBase, _MetadataMixin):
    """Asynchronous custom metadata resource."""


class _CostsMixin:
    SERVICE_NAME = "costs"
    get_resource_costs = _CatalogMethod("get_resource_costs")
    update_resource_costs = _CatalogMethod("update_resource_costs")
    list_sync_jobs = _CatalogMethod("list_sync_jobs")
    create_sync_job = _CatalogMethod("create_sync_job")
    run_sync_job = _CatalogMethod("run_sync_job")


class CostsResource(SyncResourceBase, _CostsMixin):
    """Synchronous cost tracking resource."""


class AsyncCostsResource(AsyncResourceBase, _CostsMixin):
    """Asynchronous cost tracking resource."""


class AIResource(SyncCrudResource):
    """Synchronous AI resources facade."""

    SERVICE_NAME = "ai"


class AsyncAIResource(AsyncCrudResource):
    """Asynchronous AI resources facade."""

    SERVICE_NAME = "ai"


class _SsoMixin:
    SERVICE_NAME = "sso"
    list_idps = _CatalogMethod("list_idp_configs")
    create_idp = _CatalogMethod("create_idp_config")
    update_idp = _CatalogMethod("update_idp_config")
    delete_idp = _CatalogMethod("delete_idp_config")
    scim_config = _CatalogMethod("get_scim_config")
    create_scim_config = _CatalogMethod("create_scim_config")
    regenerate_scim_token = _CatalogMethod("regenerate_scim_token")
    scim_list_users = _CatalogMethod("scim_list_users")
    scim_create_user = _CatalogMethod("scim_create_user")
    scim_get_user = _CatalogMethod("scim_get_user")
    scim_update_user = _CatalogMethod("scim_update_user")
    scim_delete_user = _CatalogMethod("scim_delete_user")
    saml_metadata = _CatalogMethod("get_sp_metadata")
    saml_tenant_metadata = _CatalogMethod("get_tenant_sp_metadata")
    saml_login = _CatalogMethod("saml_login")
    saml_acs = _CatalogMethod("saml_acs")
    oidc_authorize = _CatalogMethod("oidc_authorize")
    oidc_callback = _CatalogMethod("oidc_callback")
    oidc_userinfo = _CatalogMethod("oidc_userinfo")
    oidc_refresh = _CatalogMethod("oidc_refresh")
    oidc_logout = _CatalogMethod("oidc_logout")


class SsoResource(SyncResourceBase, _SsoMixin):
    """Synchronous SSO (SAML/OIDC/SCIM) resource."""


class AsyncSsoResource(AsyncResourceBase, _SsoMixin):
    """Asynchronous SSO (SAML/OIDC/SCIM) resource."""


class _ApiKeysMixin:
    SERVICE_NAME = "api_keys"
    list = _CatalogMethod("list")
    create = _CatalogMethod("create")
    delete = _CatalogMethod("delete")


class ApiKeysResource(SyncResourceBase, _ApiKeysMixin):
    """Synchronous API keys resource."""


class AsyncApiKeysResource(AsyncResourceBase, _ApiKeysMixin):
    """Asynchronous API keys resource."""


class _ModulesMixin:
    SERVICE_NAME = "modules"
    list = _CatalogMethod("list")


class ModulesResource(SyncResourceBase, _ModulesMixin):
    """Synchronous modules registry resource."""


class AsyncModulesResource(AsyncResourceBase, _ModulesMixin):
    """Asynchronous modules registry resource."""


class _RunsMixin:
    SERVICE_NAME = "runs"
    invoke = _CatalogMethod(
        "invoke_run_function",
        "Queue a pending execution for a serverless function.",
    )
    list_executions = _CatalogMethod(
        "list_run_executions",
        "List executions for the current tenant with optional filters.",
    )
    get_execution = _CatalogMethod(
        "get_run_execution",
        "Fetch a single execution record including stdout/stderr logs and output.",
    )
    claim_pending = _CatalogMethod(
        "claim_pending_executions",
        "Atomically claim pending executions (internal svc-runworker endpoint).",
    )
    report_result = _CatalogMethod(
        "report_execution_result",
        "Report terminal execution output and logs (internal svc-runworker endpoint).",
    )


class RunsResource(SyncCrudResource, _RunsMixin):
    """Synchronous Elder Runs serverless functions resource."""

    def iter_executions(
        self,
        *,
        params: Mapping[str, Any] | None = None,
        per_page: int = 50,
        items_key: str | None = None,
        max_pages: int | None = None,
        **filters: Any,
    ) -> Iterator[Any]:
        """Iterate across all pages returned by ``list_executions``."""
        return self.iter_operation(
            "list_run_executions",
            params=params,
            per_page=per_page,
            items_key=items_key,
            max_pages=max_pages,
            **filters,
        )


class AsyncRunsResource(AsyncCrudResource, _RunsMixin):
    """Asynchronous Elder Runs serverless functions resource."""

    async def iter_executions(
        self,
        *,
        params: Mapping[str, Any] | None = None,
        per_page: int = 50,
        items_key: str | None = None,
        max_pages: int | None = None,
        **filters: Any,
    ) -> AsyncIterator[Any]:
        """Asynchronously iterate across all pages returned by ``list_executions``."""
        async for item in self.iter_operation(
            "list_run_executions",
            params=params,
            per_page=per_page,
            items_key=items_key,
            max_pages=max_pages,
            **filters,
        ):
            yield item


class _WorkersMixin:
    """Shared constants and request builders for Remote Workers Fleet resources."""

    SERVICE_NAME = "workers"
    _BASE_PATH = "/api/v1/admin/workers"

    @classmethod
    def _worker_path(cls, worker_id: str | int) -> str:
        raw = str(worker_id).strip()
        if not raw:
            raise ValueError("worker_id must not be empty")
        return f"{cls._BASE_PATH}/{quote(raw, safe='')}"

    @staticmethod
    def _build_list_params(
        worker_type: str | None,
        cluster: str | None,
        status: str | None,
        page: int,
        per_page: int,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page": page, "per_page": per_page}
        if worker_type is not None:
            params["worker_type"] = worker_type
        if cluster is not None:
            params["cluster"] = cluster
        if status is not None:
            params["status"] = status
        return params

    @staticmethod
    def _build_onboard_payload(
        name: str,
        worker_type: str,
        *,
        cluster: str | None = None,
        config: Mapping[str, Any] | None = None,
        capabilities: Any = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": name, "worker_type": worker_type}
        if cluster is not None:
            payload["cluster"] = cluster
        if config is not None:
            payload["config"] = dict(config)
        if capabilities is not None:
            payload["capabilities"] = capabilities
        return payload

    @staticmethod
    def _build_heartbeat_payload(
        worker_type: str,
        *,
        worker_id: str | int | None = None,
        hostname: str | None = None,
        version: str | None = None,
        cluster: str | None = None,
        capabilities: Any = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"worker_type": worker_type}
        if worker_id is not None:
            payload["worker_id"] = worker_id
        if hostname is not None:
            payload["hostname"] = hostname
        if version is not None:
            payload["version"] = version
        if cluster is not None:
            payload["cluster"] = cluster
        if capabilities is not None:
            payload["capabilities"] = capabilities
        return payload


class WorkersResource(SyncResourceBase, _WorkersMixin):
    """Synchronous Remote Workers Fleet management resource."""

    @staticmethod
    def _build_proxy(client: ElderClient, service_name: str) -> Any:
        if client.catalog.has_service(service_name):
            return ServiceProxy(client, service_name)
        return None

    def list(
        self,
        worker_type: str | None = None,
        cluster: str | None = None,
        status: str | None = None,
        page: int = 1,
        per_page: int = 50,
    ) -> Any:
        """List registered remote workers with optional filters."""
        params = self._build_list_params(worker_type, cluster, status, page, per_page)
        return self._client.get(self._BASE_PATH, params=params)

    def get(self, worker_id: str | int) -> Any:
        """Fetch a single remote worker by ID."""
        return self._client.get(self._worker_path(worker_id))

    def onboard(
        self,
        name: str,
        worker_type: str,
        *,
        cluster: str | None = None,
        config: Mapping[str, Any] | None = None,
        capabilities: Any = None,
    ) -> Any:
        """Onboard a new remote worker and return its credentials."""
        payload = self._build_onboard_payload(
            name,
            worker_type,
            cluster=cluster,
            config=config,
            capabilities=capabilities,
        )
        return self._client.post(self._BASE_PATH, json=payload)

    def update(self, worker_id: str | int, **fields: Any) -> Any:
        """Update fields on an existing remote worker."""
        return self._client.put(self._worker_path(worker_id), json=fields)

    def rotate_token(self, worker_id: str | int) -> Any:
        """Rotate the authentication token for a remote worker."""
        return self._client.post(f"{self._worker_path(worker_id)}/rotate-token")

    def offboard(self, worker_id: str | int) -> Any:
        """Offboard and remove a remote worker."""
        return self._client.delete(self._worker_path(worker_id))

    def heartbeat(
        self,
        worker_type: str,
        *,
        worker_id: str | int | None = None,
        hostname: str | None = None,
        version: str | None = None,
        cluster: str | None = None,
        capabilities: Any = None,
    ) -> Any:
        """Send a worker liveness heartbeat."""
        payload = self._build_heartbeat_payload(
            worker_type,
            worker_id=worker_id,
            hostname=hostname,
            version=version,
            cluster=cluster,
            capabilities=capabilities,
        )
        return self._client.post(f"{self._BASE_PATH}/heartbeat", json=payload)


class AsyncWorkersResource(AsyncResourceBase, _WorkersMixin):
    """Asynchronous Remote Workers Fleet management resource."""

    @staticmethod
    def _build_proxy(client: AsyncElderClient, service_name: str) -> Any:
        if client.catalog.has_service(service_name):
            return AsyncServiceProxy(client, service_name)
        return None

    async def list(
        self,
        worker_type: str | None = None,
        cluster: str | None = None,
        status: str | None = None,
        page: int = 1,
        per_page: int = 50,
    ) -> Any:
        """Asynchronously list registered remote workers with optional filters."""
        params = self._build_list_params(worker_type, cluster, status, page, per_page)
        return await self._client.get(self._BASE_PATH, params=params)

    async def get(self, worker_id: str | int) -> Any:
        """Asynchronously fetch a single remote worker by ID."""
        return await self._client.get(self._worker_path(worker_id))

    async def onboard(
        self,
        name: str,
        worker_type: str,
        *,
        cluster: str | None = None,
        config: Mapping[str, Any] | None = None,
        capabilities: Any = None,
    ) -> Any:
        """Asynchronously onboard a new remote worker and return its credentials."""
        payload = self._build_onboard_payload(
            name,
            worker_type,
            cluster=cluster,
            config=config,
            capabilities=capabilities,
        )
        return await self._client.post(self._BASE_PATH, json=payload)

    async def update(self, worker_id: str | int, **fields: Any) -> Any:
        """Asynchronously update fields on an existing remote worker."""
        return await self._client.put(self._worker_path(worker_id), json=fields)

    async def rotate_token(self, worker_id: str | int) -> Any:
        """Asynchronously rotate the authentication token for a remote worker."""
        return await self._client.post(f"{self._worker_path(worker_id)}/rotate-token")

    async def offboard(self, worker_id: str | int) -> Any:
        """Asynchronously offboard and remove a remote worker."""
        return await self._client.delete(self._worker_path(worker_id))

    async def heartbeat(
        self,
        worker_type: str,
        *,
        worker_id: str | int | None = None,
        hostname: str | None = None,
        version: str | None = None,
        cluster: str | None = None,
        capabilities: Any = None,
    ) -> Any:
        """Asynchronously send a worker liveness heartbeat."""
        payload = self._build_heartbeat_payload(
            worker_type,
            worker_id=worker_id,
            hostname=hostname,
            version=version,
            cluster=cluster,
            capabilities=capabilities,
        )
        return await self._client.post(f"{self._BASE_PATH}/heartbeat", json=payload)


SYNC_RESOURCE_CLASSES: dict[str, type[SyncResourceBase]] = {
    "auth": AuthResource,
    "organizations": OrganizationsResource,
    "entities": EntitiesResource,
    "issues": IssuesResource,
    "documents": DocumentsResource,
    "pages": PagesResource,
    "diagrams": DiagramsResource,
    "streams": StreamsResource,
    "flows": FlowsResource,
    "helpdesk": HelpdeskResource,
    "ipam": IpamResource,
    "sbom": SbomResource,
    "vulnerabilities": VulnerabilitiesResource,
    "secrets": SecretsResource,
    "keys": KeysResource,
    "certificates": CertificatesResource,
    "discovery": DiscoveryResource,
    "sync": SyncResource,
    "iam": IamResource,
    "google_workspace": GoogleWorkspaceResource,
    "on_call": OnCallResource,
    "webhooks": WebhooksResource,
    "access_reviews": AccessReviewsResource,
    "search": SearchResource,
    "licensing": LicensingResource,
    "village_id": VillageIdResource,
    "tenants": TenantsResource,
    "identities": IdentitiesResource,
    "users": UsersResource,
    "audit": AuditResource,
    "backup": BackupResource,
    "privacy": PrivacyResource,
    "data_stores": DataStoresResource,
    "dependencies": DependenciesResource,
    "networking": NetworkingResource,
    "geo": GeoResource,
    "graph": GraphResource,
    "services": ServicesResource,
    "software": SoftwareResource,
    "projects": ProjectsResource,
    "milestones": MilestonesResource,
    "labels": LabelsResource,
    "metadata": MetadataResource,
    "costs": CostsResource,
    "ai": AIResource,
    "sso": SsoResource,
    "api_keys": ApiKeysResource,
    "modules": ModulesResource,
    "runs": RunsResource,
    "workers": WorkersResource,
}

ASYNC_RESOURCE_CLASSES: dict[str, type[AsyncResourceBase]] = {
    "auth": AsyncAuthResource,
    "organizations": AsyncOrganizationsResource,
    "entities": AsyncEntitiesResource,
    "issues": AsyncIssuesResource,
    "documents": AsyncDocumentsResource,
    "pages": AsyncPagesResource,
    "diagrams": AsyncDiagramsResource,
    "streams": AsyncStreamsResource,
    "flows": AsyncFlowsResource,
    "helpdesk": AsyncHelpdeskResource,
    "ipam": AsyncIpamResource,
    "sbom": AsyncSbomResource,
    "vulnerabilities": AsyncVulnerabilitiesResource,
    "secrets": AsyncSecretsResource,
    "keys": AsyncKeysResource,
    "certificates": AsyncCertificatesResource,
    "discovery": AsyncDiscoveryResource,
    "sync": AsyncSyncResource,
    "iam": AsyncIamResource,
    "google_workspace": AsyncGoogleWorkspaceResource,
    "on_call": AsyncOnCallResource,
    "webhooks": AsyncWebhooksResource,
    "access_reviews": AsyncAccessReviewsResource,
    "search": AsyncSearchResource,
    "licensing": AsyncLicensingResource,
    "village_id": AsyncVillageIdResource,
    "tenants": AsyncTenantsResource,
    "identities": AsyncIdentitiesResource,
    "users": AsyncUsersResource,
    "audit": AsyncAuditResource,
    "backup": AsyncBackupResource,
    "privacy": AsyncPrivacyResource,
    "data_stores": AsyncDataStoresResource,
    "dependencies": AsyncDependenciesResource,
    "networking": AsyncNetworkingResource,
    "geo": AsyncGeoResource,
    "graph": AsyncGraphResource,
    "services": AsyncServicesResource,
    "software": AsyncSoftwareResource,
    "projects": AsyncProjectsResource,
    "milestones": AsyncMilestonesResource,
    "labels": AsyncLabelsResource,
    "metadata": AsyncMetadataResource,
    "costs": AsyncCostsResource,
    "ai": AsyncAIResource,
    "sso": AsyncSsoResource,
    "api_keys": AsyncApiKeysResource,
    "modules": AsyncModulesResource,
    "runs": AsyncRunsResource,
    "workers": AsyncWorkersResource,
}
