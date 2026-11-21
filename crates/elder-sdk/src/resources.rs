//! Thin ergonomic resource clients grouped by Elder service domain.

use std::borrow::Cow;
use std::collections::HashMap;
use std::fmt;

use serde::de::DeserializeOwned;
use serde_json::Value;

use crate::catalog::{normalize_name, ApiOperation, HttpMethod};
use crate::client::ElderClient;
use crate::error::{ElderError, Result};

/// Thin service-scoped client bound to an [`ElderClient`] and a canonical catalog service.
#[derive(Clone)]
pub struct ResourceClient<'a> {
    client: &'a ElderClient,
    service: Cow<'static, str>,
}

impl<'a> fmt::Debug for ResourceClient<'a> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ResourceClient")
            .field("service", &self.service)
            .finish()
    }
}

impl<'a> ResourceClient<'a> {
    /// Create a [`ResourceClient`] targeting `service` (normalized to kebab-case).
    pub(crate) fn new(client: &'a ElderClient, service: Cow<'static, str>) -> Self {
        Self { client, service }
    }

    /// Return the service name targeted by this resource client.
    pub fn service(&self) -> &str {
        self.service.as_ref()
    }

    /// List all catalog operations belonging to this resource's service.
    pub fn operations(&self) -> Vec<&'static ApiOperation> {
        self.client.catalog().operations(&self.service)
    }

    /// Look up a specific operation within this resource's service.
    pub fn operation(&self, operation: &str) -> Option<&'static ApiOperation> {
        self.client.catalog().find(&self.service, operation)
    }

    /// Invoke any operation on this resource's service by operation name.
    pub async fn invoke(
        &self,
        operation: &str,
        path_params: &HashMap<String, String>,
        query: Option<&[(String, String)]>,
        body: Option<&Value>,
    ) -> Result<Value> {
        self.client
            .invoke(&self.service, operation, path_params, query, body)
            .await
    }

    /// Invoke any operation on this resource's service and deserialize the JSON response into `T`.
    pub async fn invoke_typed<T: DeserializeOwned>(
        &self,
        operation: &str,
        path_params: &HashMap<String, String>,
        query: Option<&[(String, String)]>,
        body: Option<&Value>,
    ) -> Result<T> {
        self.client
            .invoke_typed(&self.service, operation, path_params, query, body)
            .await
    }

    /// Call the primary collection-listing `GET` operation for this service.
    pub async fn list(&self, query: Option<&[(String, String)]>) -> Result<Value> {
        let op = self.primary_list_op()?;
        self.client.request(op.method, &op.path, query, None).await
    }

    /// Paginate through all items from the primary collection-listing `GET` operation.
    pub async fn list_all(
        &self,
        query: Option<&[(String, String)]>,
        per_page: Option<u32>,
        max_pages: Option<usize>,
    ) -> Result<Vec<Value>> {
        let op = self.primary_list_op()?;
        self.client
            .paginate(&op.path, query, per_page, max_pages)
            .await
    }

    /// Fetch a single resource by identifier using the primary single-param `GET` operation.
    pub async fn get(&self, id: impl fmt::Display) -> Result<Value> {
        let op = self.primary_get_op()?;
        let params = single_param_map(op, id)?;
        self.client
            .invoke(&self.service, &op.operation, &params, None, None)
            .await
    }

    /// Create a resource using the primary `POST` operation for this service.
    pub async fn create(&self, body: &Value) -> Result<Value> {
        let op = self.primary_create_op()?;
        self.client
            .request(op.method, &op.path, None, Some(body))
            .await
    }

    /// Update a resource by identifier using the primary single-param `PATCH`/`PUT` operation.
    pub async fn update(&self, id: impl fmt::Display, body: &Value) -> Result<Value> {
        let op = self.primary_update_op()?;
        let params = single_param_map(op, id)?;
        self.client
            .invoke(&self.service, &op.operation, &params, None, Some(body))
            .await
    }

    /// Delete a resource by identifier using the primary single-param `DELETE` operation.
    pub async fn delete(&self, id: impl fmt::Display) -> Result<Value> {
        let op = self.primary_delete_op()?;
        let params = single_param_map(op, id)?;
        self.client
            .invoke(&self.service, &op.operation, &params, None, None)
            .await
    }

    fn primary_list_op(&self) -> Result<&'static ApiOperation> {
        self.select_primary_op(
            |op| op.method == HttpMethod::Get && op.path_params.is_empty(),
            |op| {
                let verb_rank = if op.operation.starts_with("list-") {
                    0_u8
                } else if op.operation.starts_with("query-") || op.operation.starts_with("search-")
                {
                    1
                } else {
                    2
                };
                (verb_rank, op.path.len(), op.operation.as_str())
            },
            "list",
        )
    }

    fn primary_get_op(&self) -> Result<&'static ApiOperation> {
        self.select_primary_op(
            |op| op.method == HttpMethod::Get && op.path_params.len() == 1,
            |op| {
                let verb_rank = if op.operation.starts_with("get-") {
                    0_u8
                } else {
                    1
                };
                let ends_with_param = u8::from(!op.path.ends_with('}'));
                (
                    verb_rank,
                    ends_with_param,
                    op.path.len(),
                    op.operation.as_str(),
                )
            },
            "get",
        )
    }

    fn primary_create_op(&self) -> Result<&'static ApiOperation> {
        self.select_primary_op(
            |op| op.method == HttpMethod::Post && op.path_params.is_empty(),
            |op| {
                let verb_rank = if op.operation.starts_with("create-") {
                    0_u8
                } else {
                    1
                };
                let body_rank = u8::from(!op.has_body);
                (verb_rank, body_rank, op.path.len(), op.operation.as_str())
            },
            "create",
        )
    }

    fn primary_update_op(&self) -> Result<&'static ApiOperation> {
        self.select_primary_op(
            |op| {
                matches!(op.method, HttpMethod::Patch | HttpMethod::Put)
                    && op.path_params.len() == 1
            },
            |op| {
                let verb_rank =
                    if op.operation.starts_with("update-") && !op.operation.ends_with("-put") {
                        0_u8
                    } else if op.operation.starts_with("update-") {
                        1
                    } else {
                        2
                    };
                let ends_with_param = u8::from(!op.path.ends_with('}'));
                (
                    verb_rank,
                    ends_with_param,
                    op.path.len(),
                    op.operation.as_str(),
                )
            },
            "update",
        )
    }

    fn primary_delete_op(&self) -> Result<&'static ApiOperation> {
        self.select_primary_op(
            |op| op.method == HttpMethod::Delete && op.path_params.len() == 1,
            |op| {
                let verb_rank =
                    if op.operation.starts_with("delete-") || op.operation.starts_with("revoke-") {
                        0_u8
                    } else {
                        1
                    };
                let ends_with_param = u8::from(!op.path.ends_with('}'));
                (
                    verb_rank,
                    ends_with_param,
                    op.path.len(),
                    op.operation.as_str(),
                )
            },
            "delete",
        )
    }

    fn select_primary_op<P, K, O>(
        &self,
        predicate: P,
        rank_key: K,
        action: &str,
    ) -> Result<&'static ApiOperation>
    where
        P: Fn(&ApiOperation) -> bool,
        K: Fn(&'static ApiOperation) -> O,
        O: Ord,
    {
        self.operations()
            .into_iter()
            .filter(|op| predicate(op))
            .min_by_key(|op| rank_key(op))
            .ok_or_else(|| {
                ElderError::Catalog(format!(
                    "service '{}' has no primary '{action}' operation; use invoke() with an explicit operation name",
                    self.service
                ))
            })
    }
}

fn single_param_map(op: &ApiOperation, id: impl fmt::Display) -> Result<HashMap<String, String>> {
    let param_name = op.path_params.first().ok_or_else(|| {
        ElderError::Catalog(format!(
            "operation '{}.{}' expects no path parameters",
            op.service, op.operation
        ))
    })?;
    let value = id.to_string();
    if value.trim().is_empty() {
        return Err(ElderError::Validation(format!(
            "parameter '{param_name}' for '{}.{}' cannot be empty",
            op.service, op.operation
        )));
    }
    let mut map = HashMap::with_capacity(1);
    map.insert(param_name.clone(), value);
    Ok(map)
}

macro_rules! resource_accessors {
    ($( $(#[$meta:meta])* $fn_name:ident => $service:literal );* $(;)?) => {
        impl ElderClient {
            /// Return a thin [`ResourceClient`] for any catalog service name
            /// (accepts `kebab-case` or `snake_case`).
            pub fn resource(&self, service: impl Into<String>) -> ResourceClient<'_> {
                ResourceClient::new(self, Cow::Owned(normalize_name(&service.into())))
            }

            $(
                $(#[$meta])*
                pub fn $fn_name(&self) -> ResourceClient<'_> {
                    ResourceClient::new(self, Cow::Borrowed($service))
                }
            )*
        }
    };
}

resource_accessors! {
    /// Access the `organizations` resource API (`/api/v1/organizations`).
    organizations => "organizations";
    /// Access the `entities` resource API (`/api/v1/entities`).
    entities => "entities";
    /// Access the `issues` resource API (`/api/v1/issues`).
    issues => "issues";
    /// Access the `documents` resource API (`/api/v1/documents` and `/api/v1/collections`).
    documents => "documents";
    /// Access the `pages` resource API (`/api/v1/pages`).
    pages => "pages";
    /// Access the `diagrams` resource API (`/api/v1/diagrams` and diagram collections/templates/libraries/storage).
    diagrams => "diagrams";
    /// Access the `streams` resource API (`/api/v1/streams` and `/api/v1/hooks`).
    streams => "streams";
    /// Access the `flows` resource API (`/api/v1/flows` and `/api/v1/flows-hooks`).
    flows => "flows";
    /// Access the `helpdesk` resource API (`/api/v1/helpdesk/*`).
    helpdesk => "helpdesk";
    /// Access the `ipam` resource API (`/api/v1/ipam/*`).
    ipam => "ipam";
    /// Access the `sbom` resource API (`/api/v1/sbom/*`).
    sbom => "sbom";
    /// Access the `vulnerabilities` resource API (`/api/v1/vulnerabilities`).
    vulnerabilities => "vulnerabilities";
    /// Access the `secrets` resource API (`/api/v1/secrets`).
    secrets => "secrets";
    /// Access the `keys` resource API (`/api/v1/keys`).
    keys => "keys";
    /// Access the `certificates` resource API (`/api/v1/certificates`).
    certificates => "certificates";
    /// Access the `discovery` resource API (`/api/v1/discovery`).
    discovery => "discovery";
    /// Access the `sync` resource API (`/api/v1/sync`).
    sync => "sync";
    /// Access the `iam` resource API (`/api/v1/iam`).
    iam => "iam";
    /// Access the `google-workspace` resource API (`/api/v1/google-workspace`).
    google_workspace => "google-workspace";
    /// Access the `on-call` resource API (`/api/v1/on-call`).
    on_call => "on-call";
    /// Access the `webhooks` resource API (`/api/v1/webhooks`).
    webhooks => "webhooks";
    /// Access the `access-reviews` resource API (`/api/v1/access-reviews`).
    access_reviews => "access-reviews";
    /// Access the `search` resource API (`/api/v1/search`).
    search => "search";
    /// Access the `tenants` resource API (`/api/v1/tenants`).
    tenants => "tenants";
    /// Access the `identities` resource API (`/api/v1/identities`).
    identities => "identities";
    /// Access the `users` resource API (`/api/v1/users`).
    users => "users";
    /// Access the `audit` resource API (`/api/v1/audit`).
    audit => "audit";
    /// Access the `backup` resource API (`/api/v1/backup`).
    backup => "backup";
    /// Access the `privacy` resource API (`/api/v1/privacy`).
    privacy => "privacy";
    /// Access the `data-stores` resource API (`/api/v1/data-stores`).
    data_stores => "data-stores";
    /// Access the `dependencies` resource API (`/api/v1/dependencies`).
    dependencies => "dependencies";
    /// Access the `networking` resource API (`/api/v1/networking`).
    networking => "networking";
    /// Access the `geo` resource API (`/api/v1/geo`).
    geo => "geo";
    /// Access the `graph` resource API (`/api/v1/graph`).
    graph => "graph";
    /// Access the `services` resource API (`/api/v1/services`).
    services => "services";
    /// Access the `software` resource API (`/api/v1/software`).
    software => "software";
    /// Access the `projects` resource API (`/api/v1/projects`).
    projects => "projects";
    /// Access the `milestones` resource API (`/api/v1/milestones`).
    milestones => "milestones";
    /// Access the `labels` resource API (`/api/v1/labels`).
    labels => "labels";
    /// Access the `metadata` resource API (`/api/v1/metadata`).
    metadata => "metadata";
    /// Access the `costs` resource API (`/api/v1/costs`).
    costs => "costs";
    /// Access the `ai` resource API (`/api/v1/ai/resources`).
    ai => "ai";
    /// Access the `sso` resource API (`/api/v1/sso`).
    sso => "sso";
    /// Access the `api-keys` resource API (`/api/v1/api-keys`).
    api_keys => "api-keys";
    /// Access the `modules` resource API (`/api/v1/modules`).
    modules => "modules";
    /// Access the `auth` resource API (`/api/v1/auth`).
    auth_resource => "auth";
    /// Access the `portal-auth` resource API (`/api/v1/portal-auth`).
    portal_auth => "portal-auth";
    /// Access the `profile` resource API (`/api/v1/profile`).
    profile => "profile";
    /// Access the `licensing` resource API (`/api/v1/licensing`).
    licensing => "licensing";
    /// Access the `logs` resource API (`/api/v1/logs`).
    logs => "logs";
    /// Access the `refs` resource API (`/api/v1/refs/*`).
    refs => "refs";
    /// Access the `lookup` resource API (`/lookup/*` and `/api/v1/id/{village_id}`).
    lookup => "lookup";
    /// Access the `entity-types` resource API (`/api/v1/entity-types`).
    entity_types => "entity-types";
    /// Access the `license-policies` resource API (`/api/v1/license-policies`).
    license_policies => "license-policies";
    /// Access the `resource-roles` resource API (`/api/v1/resource-roles`).
    resource_roles => "resource-roles";
    /// Access the `group-membership` resource API (`/api/v1/group-membership`).
    group_membership => "group-membership";
    /// Access the `builtin-secrets` resource API (`/api/v1/builtin-secrets`).
    builtin_secrets => "builtin-secrets";
    /// Access the `privacy-admin` resource API (`/api/v1/privacy-admin`).
    privacy_admin => "privacy-admin";
    /// Access the `audit-enterprise` resource API (`/api/v1/audit-enterprise`).
    audit_enterprise => "audit-enterprise";
    /// Access the `integrations-internal` resource API (`/api/v1/integrations/internal`).
    integrations_internal => "integrations-internal";
    /// Access the `import` CSV bulk-import API (`/api/v1/import`).
    csv_import => "import";
    /// Access the `system` endpoints (`/healthz`, `/api/v1/status`).
    system => "system";
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::{ClientConfig, RetryConfig};
    use serde_json::json;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::TcpListener;
    use tokio::sync::Mutex;

    async fn spawn_echo_server(
        responses: Vec<(u16, String)>,
    ) -> (String, std::sync::Arc<Mutex<Vec<(String, String, String)>>>) {
        let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
        let addr = listener.local_addr().expect("addr");
        let recorded = std::sync::Arc::new(Mutex::new(Vec::new()));
        let rec_bg = std::sync::Arc::clone(&recorded);

        tokio::spawn(async move {
            for (status, body) in responses {
                let Ok((mut sock, _)) = listener.accept().await else {
                    break;
                };
                let mut buf = vec![0u8; 8192];
                let n = sock.read(&mut buf).await.unwrap_or(0);
                let raw = String::from_utf8_lossy(&buf[..n]);
                let split = raw.find("\r\n\r\n").unwrap_or(raw.len());
                let first_line = raw[..split].lines().next().unwrap_or("");
                let mut parts = first_line.split_whitespace();
                let method = parts.next().unwrap_or("").to_string();
                let target = parts.next().unwrap_or("").to_string();
                let req_body = if split + 4 <= raw.len() {
                    raw[split + 4..].to_string()
                } else {
                    String::new()
                };
                rec_bg.lock().await.push((method, target, req_body));

                let wire = format!(
                    "HTTP/1.1 {status} OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                );
                let _ = sock.write_all(wire.as_bytes()).await;
                let _ = sock.shutdown().await;
            }
        });

        (format!("http://{addr}"), recorded)
    }

    #[test]
    fn all_resource_accessors_exist_in_embedded_catalog() {
        let client = ElderClient::new(ClientConfig::new("http://127.0.0.1:5000").unwrap()).unwrap();
        let clients = [
            client.organizations(),
            client.entities(),
            client.issues(),
            client.documents(),
            client.pages(),
            client.diagrams(),
            client.streams(),
            client.flows(),
            client.helpdesk(),
            client.ipam(),
            client.sbom(),
            client.vulnerabilities(),
            client.secrets(),
            client.keys(),
            client.certificates(),
            client.discovery(),
            client.sync(),
            client.iam(),
            client.google_workspace(),
            client.on_call(),
            client.webhooks(),
            client.access_reviews(),
            client.search(),
            client.tenants(),
            client.identities(),
            client.users(),
            client.audit(),
            client.backup(),
            client.privacy(),
            client.data_stores(),
            client.dependencies(),
            client.networking(),
            client.geo(),
            client.graph(),
            client.services(),
            client.software(),
            client.projects(),
            client.milestones(),
            client.labels(),
            client.metadata(),
            client.costs(),
            client.ai(),
            client.sso(),
            client.api_keys(),
            client.modules(),
            client.auth_resource(),
            client.portal_auth(),
            client.profile(),
            client.licensing(),
            client.logs(),
            client.refs(),
            client.lookup(),
            client.entity_types(),
            client.license_policies(),
            client.resource_roles(),
            client.group_membership(),
            client.builtin_secrets(),
            client.privacy_admin(),
            client.audit_enterprise(),
            client.integrations_internal(),
            client.csv_import(),
            client.system(),
        ];

        assert_eq!(clients.len(), client.catalog().services().len());
        for rc in &clients {
            assert!(
                !rc.operations().is_empty(),
                "resource '{}' has no operations",
                rc.service()
            );
        }

        // Generic resource() lookup also accepts snake_case and kebab-case.
        assert_eq!(
            client.resource("access_reviews").operations().len(),
            client.access_reviews().operations().len()
        );
    }

    #[tokio::test]
    async fn resource_crud_helpers_and_list_all_work_against_mock_server() {
        let (url, recorded) =
            spawn_echo_server(vec![
            (
                200,
                r#"{"items":[{"id":1,"name":"org-1"}],"total":1,"page":1,"per_page":50,"pages":1}"#
                    .into(),
            ),
            (200, r#"{"id":42,"title":"Bug"}"#.into()),
            (201, r#"{"id":43,"title":"Created"}"#.into()),
            (200, r#"{"id":42,"title":"Updated"}"#.into()),
            (200, r#"{"deleted":true}"#.into()),
            (200, r#"{"items":[{"id":1}],"total":2,"page":1,"per_page":1,"pages":2}"#.into()),
            (200, r#"{"items":[{"id":2}],"total":2,"page":2,"per_page":1,"pages":2}"#.into()),
            (200, r#"{"value":"s3cr3t"}"#.into()),
        ])
            .await;

        let client = ElderClient::new(
            ClientConfig::new(url)
                .unwrap()
                .with_bearer("tok")
                .with_retry(RetryConfig::disabled()),
        )
        .unwrap();

        let orgs = client
            .organizations()
            .list(Some(&[("search".into(), "acme".into())]))
            .await
            .expect("list orgs");
        assert_eq!(orgs["items"][0]["name"], "org-1");

        let issue = client.issues().get(42).await.expect("get issue");
        assert_eq!(issue["title"], "Bug");

        let created = client
            .entities()
            .create(&json!({"name": "web-01"}))
            .await
            .expect("create entity");
        assert_eq!(created["id"], 43);

        let updated = client
            .entities()
            .update(42, &json!({"name": "web-02"}))
            .await
            .expect("update entity");
        assert_eq!(updated["title"], "Updated");

        let deleted = client.secrets().delete(9).await.expect("delete secret");
        assert_eq!(deleted["deleted"], true);

        let all_issues = client
            .issues()
            .list_all(None, Some(1), None)
            .await
            .expect("list_all");
        assert_eq!(all_issues.len(), 2);

        let mut params = HashMap::new();
        params.insert("secret_id".into(), "9".into());
        let unmasked = client
            .secrets()
            .invoke("unmask-secret", &params, None, None)
            .await
            .expect("invoke unmask");
        assert_eq!(unmasked["value"], "s3cr3t");

        let reqs = recorded.lock().await;
        assert_eq!(reqs[0].0, "GET");
        assert_eq!(reqs[0].1, "/api/v1/organizations?search=acme");
        assert_eq!(reqs[1].0, "GET");
        assert_eq!(reqs[1].1, "/api/v1/issues/42");
        assert_eq!(reqs[2].0, "POST");
        assert_eq!(reqs[2].1, "/api/v1/entities");
        assert_eq!(reqs[3].0, "PATCH");
        assert_eq!(reqs[3].1, "/api/v1/entities/42");
        assert_eq!(reqs[4].0, "DELETE");
        assert_eq!(reqs[4].1, "/api/v1/secrets/9");
        assert_eq!(reqs[7].0, "POST");
        assert_eq!(reqs[7].1, "/api/v1/secrets/9/unmask");
    }
}
