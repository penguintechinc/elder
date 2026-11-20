//! Mock-HTTP `tokio::net::TcpListener` tests for all 7 cloud discovery providers and 3 IAM paths.

use std::collections::BTreeMap;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Duration;

use elder_int_cloud::{run_cloud_discovery, run_cloud_iam};
use elder_svc_common::{ElderApiClient, ServiceTokenProvider};
use jsonwebtoken::{decode, Algorithm, DecodingKey, Validation};
use rand::rngs::OsRng;
use rsa::pkcs8::{EncodePrivateKey, EncodePublicKey, LineEnding};
use rsa::RsaPrivateKey;
use serde::Deserialize;
use serde_json::{json, Value};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};

#[derive(Debug, Clone)]
struct RecordedRequest {
    method: String,
    target: String,
    headers: BTreeMap<String, String>,
    body: String,
}

async fn read_http_request(socket: &mut TcpStream) -> Option<RecordedRequest> {
    let mut buf: Vec<u8> = Vec::with_capacity(4096);
    let mut chunk = [0u8; 4096];

    let header_end = loop {
        let n = socket.read(&mut chunk).await.ok()?;
        if n == 0 {
            return None;
        }
        buf.extend_from_slice(&chunk[..n]);
        if let Some(pos) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
            break pos;
        }
        if buf.len() > 1024 * 1024 {
            return None;
        }
    };

    let head_str = String::from_utf8_lossy(&buf[..header_end]);
    let mut lines = head_str.split("\r\n");
    let request_line = lines.next()?;
    let mut req_parts = request_line.split_whitespace();
    let method = req_parts.next()?.to_string();
    let target = req_parts.next()?.to_string();

    let mut headers = BTreeMap::new();
    let mut content_length: usize = 0;
    for line in lines {
        if let Some((k, v)) = line.split_once(':') {
            let key = k.trim().to_ascii_lowercase();
            let val = v.trim().to_string();
            if key == "content-length" {
                content_length = val.parse::<usize>().unwrap_or(0);
            }
            headers.insert(key, val);
        }
    }

    let body_start = header_end + 4;
    while buf.len() < body_start + content_length {
        let n = socket.read(&mut chunk).await.ok()?;
        if n == 0 {
            break;
        }
        buf.extend_from_slice(&chunk[..n]);
    }

    let body_end = (body_start + content_length).min(buf.len());
    let body = String::from_utf8_lossy(&buf[body_start..body_end]).to_string();

    Some(RecordedRequest {
        method,
        target,
        headers,
        body,
    })
}

async fn spawn_mock_server<F>(handler: F) -> (String, Arc<Mutex<Vec<RecordedRequest>>>)
where
    F: Fn(&RecordedRequest) -> (u16, &'static str, String) + Send + Sync + 'static,
{
    let listener = TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind loopback");
    let addr = listener.local_addr().expect("local addr");
    let recorded = Arc::new(Mutex::new(Vec::new()));
    let recorded_clone = Arc::clone(&recorded);
    let handler = Arc::new(handler);

    tokio::spawn(async move {
        while let Ok((mut socket, _)) = listener.accept().await {
            let rec = Arc::clone(&recorded_clone);
            let h = Arc::clone(&handler);
            tokio::spawn(async move {
                if let Some(req) = read_http_request(&mut socket).await {
                    let (status, content_type, resp_body) = h(&req);
                    rec.lock().expect("lock recorded").push(req);
                    let reason = match status {
                        200 => "OK",
                        201 => "Created",
                        400 => "Bad Request",
                        401 => "Unauthorized",
                        403 => "Forbidden",
                        404 => "Not Found",
                        500 => "Internal Server Error",
                        _ => "Test",
                    };
                    let raw_resp = format!(
                        "HTTP/1.1 {status} {reason}\r\nContent-Type: {content_type}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{resp_body}",
                        resp_body.len()
                    );
                    let _ = socket.write_all(raw_resp.as_bytes()).await;
                    let _ = socket.shutdown().await;
                }
            });
        }
    });

    (format!("http://{addr}"), recorded)
}

fn test_elder_client(base_url: String) -> ElderApiClient {
    let auth = ServiceTokenProvider::from_env_lookup(
        "svc-integrations",
        vec![
            "discovery:read".into(),
            "discovery:write".into(),
            "integrations:sync".into(),
        ],
        |k| match k {
            "JWT_SECRET_KEY" => Some("x".repeat(32)),
            _ => None,
        },
    )
    .expect("service token provider");
    ElderApiClient::new(base_url, auth)
        .expect("elder client")
        .with_retry_config(0, Duration::from_millis(1))
}

fn parse_json_body(req: &RecordedRequest) -> Value {
    serde_json::from_str(&req.body).expect("valid JSON request body")
}

fn ephemeral_rsa_keypair() -> &'static (String, String) {
    static KEYPAIR: OnceLock<(String, String)> = OnceLock::new();
    KEYPAIR.get_or_init(|| {
        let priv_key = RsaPrivateKey::new(&mut OsRng, 2048).expect("ephemeral 2048-bit rsa key");
        let priv_pem = priv_key
            .to_pkcs8_pem(LineEnding::LF)
            .expect("pkcs8 pem")
            .to_string();
        let pub_pem = priv_key
            .to_public_key()
            .to_public_key_pem(LineEnding::LF)
            .expect("spki pem");
        (priv_pem, pub_pem)
    })
}

fn elder_discovery_handler(
    job_id: i64,
    history_id: i64,
) -> impl Fn(&RecordedRequest) -> (u16, &'static str, String) + Send + Sync + 'static {
    let start_path = format!("/api/v1/integrations/internal/discovery/{job_id}/start");
    let complete_path = format!("/api/v1/integrations/internal/discovery/{job_id}/complete");
    move |req: &RecordedRequest| {
        if req.method == "POST" && req.target == start_path {
            (
                200,
                "application/json",
                json!({ "history_id": history_id, "status": "running" }).to_string(),
            )
        } else if req.method == "POST" && req.target == complete_path {
            (200, "application/json", json!({ "ok": true }).to_string())
        } else {
            (
                404,
                "application/json",
                json!({ "error": "not found" }).to_string(),
            )
        }
    }
}

fn elder_iam_handler(
    provider_id: i64,
) -> impl Fn(&RecordedRequest) -> (u16, &'static str, String) + Send + Sync + 'static {
    let apply_path = format!("/api/v1/integrations/internal/iam/{provider_id}/apply");
    move |req: &RecordedRequest| {
        if req.method == "POST" && req.target == apply_path {
            (
                200,
                "application/json",
                json!({ "applied": true }).to_string(),
            )
        } else {
            (
                404,
                "application/json",
                json!({ "error": "not found" }).to_string(),
            )
        }
    }
}

#[tokio::test]
async fn aws_discovery_end_to_end_ec2_vpc_subnet_s3_rds_and_dependencies() {
    let (elder_url, elder_reqs) = spawn_mock_server(elder_discovery_handler(101, 5001)).await;
    let (aws_url, aws_reqs) = spawn_mock_server(|req| {
        let has_sigv4 = req
            .headers
            .get("authorization")
            .is_some_and(|a| a.starts_with("AWS4-HMAC-SHA256 "));
        let has_date = req.headers.contains_key("x-amz-date");
        let has_tok = req.headers.get("x-amz-security-token").map(String::as_str) == Some("sts-tok-1");
        if !has_sigv4 || !has_date || !has_tok {
            return (403, "application/xml", "<Error><Code>SignatureMissing</Code></Error>".into());
        }

        if req.target.contains("Action=DescribeInstances") {
            let xml = r#"<DescribeInstancesResponse xmlns="http://ec2.amazonaws.com/doc/2016-11-15/">
                <reservationSet><item><instancesSet>
                    <item>
                        <instanceId>i-0live123</instanceId>
                        <instanceType>t3.medium</instanceType>
                        <instanceState><name>running</name></instanceState>
                        <privateIpAddress>10.0.1.25</privateIpAddress>
                        <vpcId>vpc-111</vpcId>
                        <subnetId>subnet-222</subnetId>
                        <groupSet><item><groupId>sg-333</groupId></item></groupSet>
                        <tagSet><item><key>Name</key><value>web-prod-1</value></item><item><key>env</key><value>prod</value></item></tagSet>
                    </item>
                    <item>
                        <instanceId>i-0dead999</instanceId>
                        <instanceType>t3.micro</instanceType>
                        <instanceState><name>terminated</name></instanceState>
                    </item>
                </instancesSet></item></reservationSet>
            </DescribeInstancesResponse>"#;
            (200, "application/xml", xml.into())
        } else if req.target.contains("Action=DescribeVpcs") {
            let xml = r#"<DescribeVpcsResponse>
                <vpcSet><item>
                    <vpcId>vpc-111</vpcId>
                    <cidrBlock>10.0.0.0/16</cidrBlock>
                    <state>available</state>
                    <isDefault>false</isDefault>
                    <tagSet><item><key>Name</key><value>prod-vpc</value></item></tagSet>
                </item></vpcSet>
            </DescribeVpcsResponse>"#;
            (200, "application/xml", xml.into())
        } else if req.target.contains("Action=DescribeSubnets") {
            let xml = r#"<DescribeSubnetsResponse>
                <subnetSet><item>
                    <subnetId>subnet-222</subnetId>
                    <vpcId>vpc-111</vpcId>
                    <cidrBlock>10.0.1.0/24</cidrBlock>
                    <availabilityZone>us-east-1a</availabilityZone>
                    <availableIpAddressCount>250</availableIpAddressCount>
                    <tagSet><item><key>Name</key><value>prod-sub-a</value></item></tagSet>
                </item></subnetSet>
            </DescribeSubnetsResponse>"#;
            (200, "application/xml", xml.into())
        } else if req.target == "/" {
            assert!(req.headers.contains_key("x-amz-content-sha256"));
            let xml = r#"<ListAllMyBucketsResult>
                <Buckets><Bucket><Name>elder-audit-logs</Name><CreationDate>2026-01-01T00:00:00Z</CreationDate></Bucket></Buckets>
            </ListAllMyBucketsResult>"#;
            (200, "application/xml", xml.into())
        } else if req.target.contains("Action=DescribeDBInstances") {
            let xml = r#"<DescribeDBInstancesResponse>
                <DescribeDBInstancesResult><DBInstances><DBInstance>
                    <DBInstanceIdentifier>elder-pg-main</DBInstanceIdentifier>
                    <DBInstanceArn>arn:aws:rds:us-east-1:123456789012:db:elder-pg-main</DBInstanceArn>
                    <Engine>postgres</Engine>
                    <EngineVersion>16.4</EngineVersion>
                    <DBInstanceClass>db.t4g.medium</DBInstanceClass>
                    <DBInstanceStatus>available</DBInstanceStatus>
                    <AllocatedStorage>100</AllocatedStorage>
                    <Endpoint><Address>elder-pg.internal</Address><Port>5432</Port></Endpoint>
                    <DBSubnetGroup><VpcId>vpc-111</VpcId></DBSubnetGroup>
                </DBInstance></DBInstances></DescribeDBInstancesResult>
            </DescribeDBInstancesResponse>"#;
            (200, "application/xml", xml.into())
        } else {
            (404, "text/plain", "unknown".into())
        }
    })
    .await;

    let elder = test_elder_client(elder_url);
    let job = json!({
        "id": 101,
        "provider": "aws",
        "config_json": {
            "_organization_id": 9,
            "region": "us-east-1",
            "access_key_id": "AKIAEXAMPLEKEY",
            "secret_access_key": "secret-example-key",
            "session_token": "sts-tok-1",
            "endpoint_url": aws_url
        }
    });

    let outcome = run_cloud_discovery(&elder, &job).await;
    assert_eq!(outcome.status, "completed");
    assert_eq!(outcome.items_synced, 5);
    assert_eq!(outcome.items_failed, 0);
    assert!(outcome.error.is_none());

    let recorded_aws = aws_reqs.lock().expect("lock").clone();
    assert_eq!(recorded_aws.len(), 5);

    let recorded_elder = elder_reqs.lock().expect("lock").clone();
    assert_eq!(recorded_elder.len(), 2);
    assert_eq!(
        recorded_elder[0].target,
        "/api/v1/integrations/internal/discovery/101/start"
    );
    assert_eq!(
        recorded_elder[1].target,
        "/api/v1/integrations/internal/discovery/101/complete"
    );
    let complete_body = parse_json_body(&recorded_elder[1]);
    assert_eq!(complete_body["history_id"], 5001);
    assert_eq!(complete_body["status"], "completed");
    assert_eq!(complete_body["organization_id"], 9);

    let resources = complete_body["resources"].as_array().expect("resources");
    assert_eq!(resources.len(), 5);
    assert!(!resources.iter().any(|r| r["resource_id"] == "i-0dead999"));

    let deps = complete_body["dependencies"]
        .as_array()
        .expect("dependencies");
    assert!(deps.iter().any(|d| {
        d["source_external_id"] == "i-0live123"
            && d["target_external_id"] == "vpc-111"
            && d["dependency_type"] == "in_network"
    }));
    assert!(deps.iter().any(|d| {
        d["source_external_id"] == "i-0live123"
            && d["target_external_id"] == "subnet-222"
            && d["dependency_type"] == "in_subnet"
    }));
    assert!(deps.iter().any(|d| {
        d["source_external_id"] == "subnet-222"
            && d["target_external_id"] == "vpc-111"
            && d["dependency_type"] == "in_network"
    }));
}

#[tokio::test]
async fn aws_discovery_supports_per_service_endpoint_urls_and_service_filter() {
    let (elder_url, elder_reqs) = spawn_mock_server(elder_discovery_handler(102, 5002)).await;
    let (s3_url, s3_reqs) = spawn_mock_server(|_req| {
        let xml = r#"<ListAllMyBucketsResult><Buckets><Bucket><Name>custom-s3-compat</Name></Bucket></Buckets></ListAllMyBucketsResult>"#;
        (200, "application/xml", xml.into())
    })
    .await;

    let elder = test_elder_client(elder_url);
    let job = json!({
        "id": 102,
        "provider": "aws",
        "config_json": {
            "region": "us-west-2",
            "access_key_id": "AKIAEXAMPLEKEY",
            "secret_access_key": "secret-example-key",
            "s3_endpoint_url": s3_url,
            "services": ["s3"]
        }
    });

    let outcome = run_cloud_discovery(&elder, &job).await;
    assert_eq!(outcome.status, "completed");
    assert_eq!(outcome.items_synced, 1);
    assert_eq!(s3_reqs.lock().expect("lock").len(), 1);

    let elder_calls = elder_reqs.lock().expect("lock").clone();
    let complete = parse_json_body(&elder_calls[1]);
    assert_eq!(complete["resources"][0]["resource_type"], "s3_bucket");
    assert_eq!(complete["resources"][0]["name"], "custom-s3-compat");
}

#[tokio::test]
async fn aws_discovery_partial_failure_still_completes_with_partial_status() {
    let (elder_url, elder_reqs) = spawn_mock_server(elder_discovery_handler(103, 5003)).await;
    let (aws_url, _aws_reqs) = spawn_mock_server(|req| {
        if req.target.contains("Action=DescribeInstances") {
            (200, "application/xml", "<DescribeInstancesResponse><reservationSet/></DescribeInstancesResponse>".into())
        } else if req.target.contains("Action=DescribeVpcs") {
            (200, "application/xml", "<DescribeVpcsResponse><vpcSet/></DescribeVpcsResponse>".into())
        } else if req.target.contains("Action=DescribeSubnets") {
            (200, "application/xml", "<DescribeSubnetsResponse><subnetSet/></DescribeSubnetsResponse>".into())
        } else if req.target == "/" {
            (200, "application/xml", "<ListAllMyBucketsResult><Buckets><Bucket><Name>b1</Name></Bucket></Buckets></ListAllMyBucketsResult>".into())
        } else {
            (403, "application/xml", "<ErrorResponse><Error><Message>AccessDenied</Message></Error></ErrorResponse>".into())
        }
    })
    .await;

    let elder = test_elder_client(elder_url);
    let job = json!({
        "id": 103,
        "provider": "aws",
        "config_json": {
            "region": "us-east-1",
            "access_key_id": "AKIAEXAMPLEKEY",
            "secret_access_key": "secret-example-key",
            "endpoint_url": aws_url
        }
    });

    let outcome = run_cloud_discovery(&elder, &job).await;
    assert_eq!(outcome.status, "partial");
    assert_eq!(outcome.items_synced, 1);
    assert_eq!(outcome.items_failed, 1);
    assert!(outcome.error.as_deref().unwrap_or("").contains("403"));

    let complete = parse_json_body(&elder_reqs.lock().expect("lock")[1]);
    assert_eq!(complete["status"], "partial");
    assert_eq!(complete["resources"].as_array().expect("arr").len(), 1);
    assert!(complete["error_message"]
        .as_str()
        .unwrap_or("")
        .contains("403"));
}

#[tokio::test]
async fn gcp_discovery_end_to_end_with_runtime_rs256_oauth_and_resources() {
    #[derive(Debug, Deserialize)]
    struct Claims {
        iss: String,
        sub: String,
        scope: String,
        aud: String,
    }

    let (priv_pem, pub_pem) = ephemeral_rsa_keypair();
    let pub_pem_owned = pub_pem.clone();
    let (elder_url, elder_reqs) = spawn_mock_server(elder_discovery_handler(104, 5004)).await;

    let (gcp_url, gcp_reqs) = spawn_mock_server(move |req| {
        if req.method == "POST" && req.target == "/token" {
            let assertion = req
                .body
                .split('&')
                .find_map(|kv| kv.strip_prefix("assertion="))
                .expect("assertion param");
            let mut val = Validation::new(Algorithm::RS256);
            val.validate_aud = false;
            let decoded = decode::<Claims>(
                assertion,
                &DecodingKey::from_rsa_pem(pub_pem_owned.as_bytes()).expect("pub key"),
                &val,
            )
            .expect("valid RS256 assertion");
            assert_eq!(decoded.claims.iss, "scanner@elder-proj.iam.gserviceaccount.com");
            assert_eq!(decoded.claims.sub, "scanner@elder-proj.iam.gserviceaccount.com");
            assert_eq!(
                decoded.claims.scope,
                "https://www.googleapis.com/auth/cloud-platform.read-only"
            );
            assert!(decoded.claims.aud.ends_with("/token"));

            return (
                200,
                "application/json",
                json!({ "access_token": "gcp-oauth-tok", "token_type": "Bearer", "expires_in": 3600 }).to_string(),
            );
        }

        if req.headers.get("authorization").map(String::as_str) != Some("Bearer gcp-oauth-tok") {
            return (401, "application/json", json!({ "error": "unauthorized" }).to_string());
        }

        if req.target == "/compute/v1/projects/elder-proj/global/networks" {
            (
                200,
                "application/json",
                json!({
                    "items": [{
                        "id": "9001",
                        "name": "corp-vpc",
                        "autoCreateSubnetworks": false,
                        "selfLink": "https://www.googleapis.com/compute/v1/projects/elder-proj/global/networks/corp-vpc",
                        "routingConfig": { "routingMode": "REGIONAL" }
                    }]
                })
                .to_string(),
            )
        } else if req.target == "/compute/v1/projects/elder-proj/aggregated/instances" {
            (
                200,
                "application/json",
                json!({
                    "items": {
                        "zones/us-central1-a": {
                            "instances": [{
                                "id": "8001",
                                "name": "gce-app-1",
                                "machineType": "zones/us-central1-a/machineTypes/e2-standard-2",
                                "status": "RUNNING",
                                "zone": "projects/elder-proj/zones/us-central1-a",
                                "labels": { "tier": "backend" },
                                "networkInterfaces": [{
                                    "network": "https://www.googleapis.com/compute/v1/projects/elder-proj/global/networks/corp-vpc",
                                    "subnetwork": "projects/elder-proj/regions/us-central1/subnetworks/corp-sub-1",
                                    "networkIP": "10.10.0.5",
                                    "accessConfigs": [{ "natIP": "34.1.2.3" }]
                                }]
                            }]
                        }
                    }
                })
                .to_string(),
            )
        } else if req.target == "/storage/v1/b?project=elder-proj" {
            (
                200,
                "application/json",
                json!({
                    "items": [{
                        "name": "elder-gcp-artifacts",
                        "location": "US",
                        "storageClass": "STANDARD",
                        "timeCreated": "2026-02-01T00:00:00Z",
                        "labels": { "team": "platform" }
                    }]
                })
                .to_string(),
            )
        } else if req.target == "/sql/v1beta4/projects/elder-proj/instances" {
            (
                200,
                "application/json",
                json!({
                    "items": [{
                        "name": "elder-sql-1",
                        "connectionName": "elder-proj:us-central1:elder-sql-1",
                        "databaseVersion": "POSTGRES_16",
                        "region": "us-central1",
                        "state": "RUNNABLE",
                        "settings": {
                            "tier": "db-custom-2-7680",
                            "ipConfiguration": {
                                "privateNetwork": "projects/elder-proj/global/networks/corp-vpc"
                            }
                        }
                    }]
                })
                .to_string(),
            )
        } else {
            (404, "application/json", json!({ "error": "not found" }).to_string())
        }
    })
    .await;

    let token_uri = format!("{gcp_url}/token");
    let creds_json = json!({
        "type": "service_account",
        "project_id": "elder-proj",
        "client_email": "scanner@elder-proj.iam.gserviceaccount.com",
        "private_key_id": "ephemeral-kid",
        "private_key": priv_pem,
        "token_uri": token_uri
    });

    let elder = test_elder_client(elder_url);
    let job = json!({
        "id": 104,
        "provider": "gcp",
        "organization_id": 12,
        "config_json": {
            "project_id": "elder-proj",
            "api_base_url": gcp_url,
            "credentials_json": creds_json.to_string()
        }
    });

    let outcome = run_cloud_discovery(&elder, &job).await;
    assert_eq!(outcome.status, "completed");
    assert_eq!(outcome.items_synced, 4);
    assert_eq!(outcome.items_failed, 0);
    assert_eq!(gcp_reqs.lock().expect("lock").len(), 5);

    let complete = parse_json_body(&elder_reqs.lock().expect("lock")[1]);
    assert_eq!(complete["status"], "completed");
    assert_eq!(complete["organization_id"], 12);
    let deps = complete["dependencies"].as_array().expect("deps");
    assert!(deps.iter().any(|d| {
        d["source_external_id"] == "8001"
            && d["target_external_id"] == "9001"
            && d["dependency_type"] == "in_network"
    }));
    assert!(deps.iter().any(|d| {
        d["source_external_id"] == "elder-proj:us-central1:elder-sql-1"
            && d["target_external_id"] == "9001"
            && d["dependency_type"] == "in_network"
    }));
}

#[tokio::test]
async fn azure_discovery_end_to_end_aad_and_arm_resources() {
    let (elder_url, elder_reqs) = spawn_mock_server(elder_discovery_handler(105, 5005)).await;
    let (az_url, az_reqs) = spawn_mock_server(|req| {
        if req.method == "POST" && req.target == "/tenant-abc/oauth2/v2.0/token" {
            assert!(req.body.contains("grant_type=client_credentials"));
            assert!(req.body.contains("client_id=client-123"));
            assert!(req.body.contains("client_secret=secret-456"));
            return (
                200,
                "application/json",
                json!({ "access_token": "az-arm-tok" }).to_string(),
            );
        }

        if req.headers.get("authorization").map(String::as_str) != Some("Bearer az-arm-tok") {
            return (401, "application/json", json!({ "error": "unauthorized" }).to_string());
        }

        if req.target.starts_with("/subscriptions/sub-1/resourcegroups") {
            (
                200,
                "application/json",
                json!({
                    "value": [{
                        "id": "/subscriptions/sub-1/resourceGroups/prod-rg",
                        "name": "prod-rg",
                        "location": "eastus",
                        "properties": { "provisioningState": "Succeeded" }
                    }]
                })
                .to_string(),
            )
        } else if req.target.contains("Microsoft.Compute/virtualMachines") && !req.target.contains("page=2") {
            (
                200,
                "application/json",
                json!({
                    "value": [{
                        "id": "/subscriptions/sub-1/resourceGroups/prod-rg/providers/Microsoft.Compute/virtualMachines/vm-1",
                        "name": "vm-1",
                        "location": "eastus",
                        "tags": { "role": "api" },
                        "properties": {
                            "hardwareProfile": { "vmSize": "Standard_D2s_v3" },
                            "storageProfile": { "osDisk": { "osType": "Linux" } },
                            "provisioningState": "Succeeded"
                        }
                    }],
                    "nextLink": "/subscriptions/sub-1/providers/Microsoft.Compute/virtualMachines?api-version=2024-03-01&page=2"
                })
                .to_string(),
            )
        } else if req.target.contains("Microsoft.Compute/virtualMachines") && req.target.contains("page=2") {
            (
                200,
                "application/json",
                json!({
                    "value": [{
                        "id": "/subscriptions/sub-1/resourceGroups/prod-rg/providers/Microsoft.Compute/virtualMachines/vm-2",
                        "name": "vm-2",
                        "location": "eastus"
                    }]
                })
                .to_string(),
            )
        } else if req.target.contains("Microsoft.Network/virtualNetworks") {
            (
                200,
                "application/json",
                json!({
                    "value": [{
                        "id": "/subscriptions/sub-1/resourceGroups/prod-rg/providers/Microsoft.Network/virtualNetworks/vnet-1",
                        "name": "vnet-1",
                        "location": "eastus",
                        "properties": { "addressSpace": { "addressPrefixes": ["10.20.0.0/16"] }, "subnets": [{}] }
                    }]
                })
                .to_string(),
            )
        } else if req.target.contains("Microsoft.Storage/storageAccounts") {
            (
                200,
                "application/json",
                json!({
                    "value": [{
                        "id": "/subscriptions/sub-1/resourceGroups/prod-rg/providers/Microsoft.Storage/storageAccounts/stprod01",
                        "name": "stprod01",
                        "location": "eastus",
                        "kind": "StorageV2",
                        "sku": { "name": "Standard_LRS" }
                    }]
                })
                .to_string(),
            )
        } else if req.target.contains("Microsoft.Sql/servers") {
            (
                200,
                "application/json",
                json!({
                    "value": [{
                        "id": "/subscriptions/sub-1/resourceGroups/prod-rg/providers/Microsoft.Sql/servers/sqlprod01",
                        "name": "sqlprod01",
                        "location": "eastus",
                        "properties": { "fullyQualifiedDomainName": "sqlprod01.database.windows.net", "version": "12.0", "state": "Ready" }
                    }]
                })
                .to_string(),
            )
        } else {
            (404, "application/json", json!({ "error": "not found" }).to_string())
        }
    })
    .await;

    let elder = test_elder_client(elder_url);
    let job = json!({
        "id": 105,
        "provider": "azure",
        "config_json": {
            "subscription_id": "sub-1",
            "tenant_id": "tenant-abc",
            "client_id": "client-123",
            "client_secret": "secret-456",
            "login_base": az_url,
            "arm_base_url": az_url
        }
    });

    let outcome = run_cloud_discovery(&elder, &job).await;
    assert_eq!(outcome.status, "completed");
    assert_eq!(outcome.items_synced, 6);
    assert_eq!(outcome.items_failed, 0);
    assert_eq!(az_reqs.lock().expect("lock").len(), 7);

    let complete = parse_json_body(&elder_reqs.lock().expect("lock")[1]);
    let deps = complete["dependencies"].as_array().expect("deps");
    assert!(deps.iter().any(|d| {
        d["source_external_id"] == "/subscriptions/sub-1/resourceGroups/prod-rg/providers/Microsoft.Compute/virtualMachines/vm-1"
            && d["target_external_id"] == "/subscriptions/sub-1/resourceGroups/prod-rg"
            && d["dependency_type"] == "in_resource_group"
    }));
}

#[tokio::test]
async fn k8s_discovery_end_to_end_all_6_resource_types_and_dependencies() {
    let (elder_url, elder_reqs) = spawn_mock_server(elder_discovery_handler(106, 5006)).await;
    let (k8s_url, k8s_reqs) = spawn_mock_server(|req| {
        if req.headers.get("authorization").map(String::as_str) != Some("Bearer k8s-tok-1") {
            return (401, "application/json", json!({ "kind": "Status" }).to_string());
        }
        match req.target.as_str() {
            "/api/v1/nodes" => (
                200,
                "application/json",
                json!({
                    "items": [{
                        "metadata": { "uid": "node-uid-1", "name": "worker-1", "labels": { "role": "worker" } },
                        "spec": { "taints": [] },
                        "status": {
                            "capacity": { "cpu": "4", "memory": "16Gi" },
                            "nodeInfo": { "kubeletVersion": "v1.31.1", "architecture": "amd64" },
                            "conditions": [{ "type": "Ready" }]
                        }
                    }]
                })
                .to_string(),
            ),
            "/api/v1/namespaces" => (
                200,
                "application/json",
                json!({
                    "items": [{
                        "metadata": { "uid": "ns-uid-prod", "name": "prod" },
                        "status": { "phase": "Active" }
                    }]
                })
                .to_string(),
            ),
            "/apis/apps/v1/deployments" => (
                200,
                "application/json",
                json!({
                    "items": [{
                        "metadata": { "uid": "dep-uid-1", "name": "api-deploy", "namespace": "prod" },
                        "spec": {
                            "replicas": 2,
                            "selector": { "matchLabels": { "app": "api" } },
                            "template": { "spec": { "containers": [{ "image": "ghcr.io/penguintechinc/api:1.0" }] } }
                        },
                        "status": { "availableReplicas": 2, "readyReplicas": 2 }
                    }]
                })
                .to_string(),
            ),
            "/api/v1/pods" => (
                200,
                "application/json",
                json!({
                    "items": [{
                        "metadata": { "uid": "pod-uid-1", "name": "api-pod-abc", "namespace": "prod" },
                        "spec": {
                            "nodeName": "worker-1",
                            "containers": [{
                                "name": "api",
                                "image": "ghcr.io/penguintechinc/api:1.0",
                                "resources": {
                                    "requests": { "cpu": "250m", "memory": "256Mi" },
                                    "limits": { "cpu": "1", "memory": "1Gi" }
                                }
                            }]
                        },
                        "status": { "phase": "Running", "podIP": "10.244.0.10" }
                    }]
                })
                .to_string(),
            ),
            "/api/v1/services" => (
                200,
                "application/json",
                json!({
                    "items": [{
                        "metadata": { "uid": "svc-uid-1", "name": "api-svc", "namespace": "prod" },
                        "spec": { "type": "ClusterIP", "clusterIP": "10.96.0.10", "ports": [{ "port": 8080, "protocol": "TCP" }] }
                    }]
                })
                .to_string(),
            ),
            "/apis/networking.k8s.io/v1/ingresses" => (
                200,
                "application/json",
                json!({
                    "items": [{
                        "metadata": { "uid": "ing-uid-1", "name": "api-ing", "namespace": "prod" },
                        "spec": {
                            "ingressClassName": "cilium",
                            "rules": [{
                                "host": "api.example.com",
                                "http": {
                                    "paths": [{
                                        "path": "/",
                                        "pathType": "Prefix",
                                        "backend": { "service": { "name": "api-svc", "port": { "number": 8080 } } }
                                    }]
                                }
                            }]
                        }
                    }]
                })
                .to_string(),
            ),
            _ => (404, "application/json", json!({}).to_string()),
        }
    })
    .await;

    let elder = test_elder_client(elder_url);
    let job = json!({
        "id": 106,
        "provider": "kubernetes",
        "config_json": {
            "api_server": k8s_url,
            "token": "k8s-tok-1",
            "verify_ssl": false
        }
    });

    let outcome = run_cloud_discovery(&elder, &job).await;
    assert_eq!(outcome.status, "completed");
    assert_eq!(outcome.items_synced, 6);
    assert_eq!(outcome.items_failed, 0);
    assert_eq!(k8s_reqs.lock().expect("lock").len(), 6);

    let complete = parse_json_body(&elder_reqs.lock().expect("lock")[1]);
    let deps = complete["dependencies"].as_array().expect("deps");
    assert!(deps.iter().any(|d| {
        d["source_external_id"] == "pod-uid-1"
            && d["target_external_id"] == "ns-uid-prod"
            && d["dependency_type"] == "runs_in"
    }));
    assert!(deps.iter().any(|d| {
        d["source_external_id"] == "pod-uid-1"
            && d["target_external_id"] == "node-uid-1"
            && d["dependency_type"] == "runs_on"
    }));
    assert!(deps.iter().any(|d| {
        d["source_external_id"] == "ing-uid-1"
            && d["target_external_id"] == "svc-uid-1"
            && d["dependency_type"] == "routes_to"
    }));
}

#[tokio::test]
async fn k8s_discovery_reads_token_file_when_inline_token_absent() {
    let tok_path = std::env::temp_dir().join(format!(
        "elder-int-cloud-k8s-tok-{}.txt",
        std::process::id()
    ));
    std::fs::write(&tok_path, "file-based-sa-token\n").expect("write temp token");

    let (elder_url, _elder_reqs) = spawn_mock_server(elder_discovery_handler(107, 5007)).await;
    let (k8s_url, k8s_reqs) = spawn_mock_server(|req| {
        if req.headers.get("authorization").map(String::as_str)
            != Some("Bearer file-based-sa-token")
        {
            return (401, "application/json", json!({}).to_string());
        }
        (200, "application/json", json!({ "items": [] }).to_string())
    })
    .await;

    let elder = test_elder_client(elder_url);
    let job = json!({
        "id": 107,
        "provider": "kubernetes",
        "config_json": {
            "endpoint": k8s_url,
            "token_file": tok_path.to_string_lossy()
        }
    });

    let outcome = run_cloud_discovery(&elder, &job).await;
    let _ = std::fs::remove_file(&tok_path);

    assert_eq!(outcome.status, "completed");
    assert_eq!(outcome.items_failed, 0);
    assert_eq!(k8s_reqs.lock().expect("lock").len(), 6);
}

#[tokio::test]
async fn vultr_discovery_end_to_end_all_5_endpoints_and_dependencies() {
    let (elder_url, elder_reqs) = spawn_mock_server(elder_discovery_handler(108, 5008)).await;
    let (vultr_url, vultr_reqs) = spawn_mock_server(|req| {
        if req.headers.get("authorization").map(String::as_str) != Some("Bearer vultr-key-1") {
            return (401, "application/json", json!({}).to_string());
        }
        match req.target.as_str() {
            "/v2/instances" => (
                200,
                "application/json",
                json!({
                    "instances": [{
                        "id": "v-inst-1",
                        "label": "edge-gw-1",
                        "region": "dfw",
                        "status": "active",
                        "plan": "vc2-2c-4gb",
                        "main_ip": "198.51.100.10",
                        "vpc_ids": ["v-vpc-1"]
                    }]
                })
                .to_string(),
            ),
            "/v2/vpcs" => (
                200,
                "application/json",
                json!({
                    "vpcs": [{
                        "id": "v-vpc-1",
                        "description": "dal-private-vpc",
                        "region": "dfw",
                        "v4_subnet": "10.99.0.0",
                        "v4_subnet_mask": 24
                    }]
                })
                .to_string(),
            ),
            "/v2/blocks" => (
                200,
                "application/json",
                json!({
                    "blocks": [{
                        "id": "v-blk-1",
                        "label": "data-vol-1",
                        "region": "dfw",
                        "size_gb": 100,
                        "status": "active",
                        "attached_to_instance": "v-inst-1"
                    }]
                })
                .to_string(),
            ),
            "/v2/bare-metals" => (
                200,
                "application/json",
                json!({
                    "bare_metals": [{
                        "id": "v-bm-1",
                        "label": "metal-1",
                        "region": "dfw",
                        "status": "active",
                        "vpc_id": "v-vpc-1"
                    }]
                })
                .to_string(),
            ),
            "/v2/kubernetes/clusters" => (
                200,
                "application/json",
                json!({
                    "vke_clusters": [{
                        "id": "v-vke-1",
                        "label": "vke-prod",
                        "region": "dfw",
                        "version": "v1.31.0+1",
                        "status": "active"
                    }]
                })
                .to_string(),
            ),
            _ => (404, "application/json", json!({}).to_string()),
        }
    })
    .await;

    let elder = test_elder_client(elder_url);
    let job = json!({
        "id": 108,
        "provider": "vultr",
        "config_json": {
            "api_key": "vultr-key-1",
            "api_base_url": vultr_url
        }
    });

    let outcome = run_cloud_discovery(&elder, &job).await;
    assert_eq!(outcome.status, "completed");
    assert_eq!(outcome.items_synced, 5);
    assert_eq!(outcome.items_failed, 0);
    assert_eq!(vultr_reqs.lock().expect("lock").len(), 5);

    let complete = parse_json_body(&elder_reqs.lock().expect("lock")[1]);
    let deps = complete["dependencies"].as_array().expect("deps");
    assert!(deps.iter().any(|d| {
        d["source_external_id"] == "v-inst-1"
            && d["target_external_id"] == "v-vpc-1"
            && d["dependency_type"] == "in_network"
    }));
    assert!(deps.iter().any(|d| {
        d["source_external_id"] == "v-blk-1"
            && d["target_external_id"] == "v-inst-1"
            && d["dependency_type"] == "attached_to"
    }));
}

#[tokio::test]
async fn lxd_discovery_end_to_end_all_4_endpoints_and_dependencies() {
    let (elder_url, elder_reqs) = spawn_mock_server(elder_discovery_handler(109, 5009)).await;
    let (lxd_url, lxd_reqs) = spawn_mock_server(|req| {
        if req.headers.get("authorization").map(String::as_str) != Some("Bearer lxd-trust-tok")
            || req.headers.get("x-lxd-project").map(String::as_str) != Some("default")
        {
            return (403, "application/json", json!({ "error": "forbidden" }).to_string());
        }

        match req.target.as_str() {
            "/1.0/instances?recursion=1" => (
                200,
                "application/json",
                json!({
                    "type": "sync",
                    "metadata": [
                        {
                            "name": "web-ct-1",
                            "project": "default",
                            "type": "container",
                            "status": "Running",
                            "architecture": "x86_64",
                            "config": { "image.os": "Ubuntu", "image.release": "24.04" },
                            "devices": {
                                "eth0": { "type": "nic", "network": "lxdbr0" },
                                "root": { "type": "disk", "pool": "zfs-pool" }
                            }
                        },
                        {
                            "name": "db-vm-1",
                            "project": "default",
                            "type": "virtual-machine",
                            "status": "Running"
                        }
                    ]
                })
                .to_string(),
            ),
            "/1.0/projects?recursion=1" => (
                200,
                "application/json",
                json!({ "type": "sync", "metadata": [{ "name": "default", "description": "Default LXD project" }] }).to_string(),
            ),
            "/1.0/networks?recursion=1" => (
                200,
                "application/json",
                json!({ "type": "sync", "metadata": [{ "name": "lxdbr0", "type": "bridge", "managed": true, "status": "Created" }] }).to_string(),
            ),
            "/1.0/storage-pools?recursion=1" => (
                200,
                "application/json",
                json!({ "type": "sync", "metadata": [{ "name": "zfs-pool", "driver": "zfs", "status": "Created" }] }).to_string(),
            ),
            _ => (404, "application/json", json!({}).to_string()),
        }
    })
    .await;

    let elder = test_elder_client(elder_url);
    let job = json!({
        "id": 109,
        "provider": "lxd",
        "config_json": {
            "url": lxd_url,
            "trust_token": "lxd-trust-tok",
            "auth_header_name": "X-LXD-Project",
            "auth_header_value": "default"
        }
    });

    let outcome = run_cloud_discovery(&elder, &job).await;
    assert_eq!(outcome.status, "completed");
    assert_eq!(outcome.items_synced, 5);
    assert_eq!(outcome.items_failed, 0);
    assert_eq!(lxd_reqs.lock().expect("lock").len(), 4);

    let complete = parse_json_body(&elder_reqs.lock().expect("lock")[1]);
    let resources = complete["resources"].as_array().expect("resources");
    assert!(resources
        .iter()
        .any(|r| r["resource_type"] == "lxd_container"));
    assert!(resources.iter().any(|r| r["resource_type"] == "lxd_vm"));
    let deps = complete["dependencies"].as_array().expect("deps");
    assert!(deps.iter().any(|d| {
        d["source_external_id"] == "web-ct-1"
            && d["target_external_id"] == "lxdbr0"
            && d["dependency_type"] == "in_network"
    }));
    assert!(deps.iter().any(|d| {
        d["source_external_id"] == "web-ct-1"
            && d["target_external_id"] == "zfs-pool"
            && d["dependency_type"] == "attached_to"
    }));
}

#[tokio::test]
async fn vcenter_discovery_end_to_end_session_auth_and_all_5_endpoints() {
    let (elder_url, elder_reqs) = spawn_mock_server(elder_discovery_handler(110, 5010)).await;
    let (vc_url, vc_reqs) = spawn_mock_server(|req| {
        if req.method == "POST" && req.target == "/api/session" {
            let auth = req.headers.get("authorization").cloned().unwrap_or_default();
            assert!(auth.starts_with("Basic "));
            return (200, "application/json", json!("vc-sess-xyz").to_string());
        }
        if req.method == "DELETE" && req.target == "/api/session" {
            return (200, "application/json", json!({}).to_string());
        }
        if req.headers.get("vmware-api-session-id").map(String::as_str) != Some("vc-sess-xyz") {
            return (401, "application/json", json!({}).to_string());
        }

        match req.target.as_str() {
            "/api/vcenter/vm" => (
                200,
                "application/json",
                json!([{ "vm": "vm-10", "name": "app-vm-1", "power_state": "POWERED_ON", "cpu_count": 4, "memory_size_MiB": 8192, "host": "host-20" }]).to_string(),
            ),
            "/api/vcenter/host" => (
                200,
                "application/json",
                json!([{ "host": "host-20", "name": "esxi-01.lab", "connection_state": "CONNECTED", "power_state": "POWERED_ON", "cluster": "domain-c30" }]).to_string(),
            ),
            "/api/vcenter/cluster" => (
                200,
                "application/json",
                json!([{ "cluster": "domain-c30", "name": "prod-cluster", "ha_enabled": true, "drs_enabled": true }]).to_string(),
            ),
            "/api/vcenter/network" => (
                200,
                "application/json",
                json!([{ "network": "network-40", "name": "VM Network", "type": "STANDARD_PORTGROUP" }]).to_string(),
            ),
            "/api/vcenter/datastore" => (
                200,
                "application/json",
                json!([{ "datastore": "datastore-50", "name": "nvme-ds-1", "type": "VMFS", "free_space": 500000000_u64, "capacity": 1000000000_u64 }]).to_string(),
            ),
            _ => (404, "application/json", json!({}).to_string()),
        }
    })
    .await;

    let elder = test_elder_client(elder_url);
    let job = json!({
        "id": 110,
        "provider": "vcenter",
        "config_json": {
            "url": vc_url,
            "username": "administrator@vsphere.local",
            "password": "test-vc-password",
            "verify_ssl": false
        }
    });

    let outcome = run_cloud_discovery(&elder, &job).await;
    assert_eq!(outcome.status, "completed");
    assert_eq!(outcome.items_synced, 5);
    assert_eq!(outcome.items_failed, 0);

    let vc_calls = vc_reqs.lock().expect("lock").clone();
    assert_eq!(vc_calls.len(), 7);

    let complete = parse_json_body(&elder_reqs.lock().expect("lock")[1]);
    let deps = complete["dependencies"].as_array().expect("deps");
    assert!(deps.iter().any(|d| {
        d["source_external_id"] == "vm-10"
            && d["target_external_id"] == "host-20"
            && d["dependency_type"] == "runs_on"
    }));
    assert!(deps.iter().any(|d| {
        d["source_external_id"] == "host-20"
            && d["target_external_id"] == "domain-c30"
            && d["dependency_type"] == "runs_in"
    }));
}

#[tokio::test]
async fn aws_iam_sync_end_to_end_users_groups_roles_memberships() {
    let (elder_url, elder_reqs) = spawn_mock_server(elder_iam_handler(301)).await;
    let (iam_url, iam_reqs) = spawn_mock_server(|req| {
        let has_sigv4 = req
            .headers
            .get("authorization")
            .is_some_and(|a| a.starts_with("AWS4-HMAC-SHA256 "));
        if !has_sigv4 || !req.headers.contains_key("x-amz-date") {
            return (403, "application/xml", "<Error/>".into());
        }

        if req.target.contains("Action=ListUsers") && !req.target.contains("Marker=page2") {
            let xml = r#"<ListUsersResponse><ListUsersResult>
                <Users><member><UserId>AIDA1</UserId><UserName>alice</UserName><Arn>arn:aws:iam::111:user/alice</Arn><Path>/</Path><CreateDate>2026-01-01T00:00:00Z</CreateDate></member></Users>
                <IsTruncated>true</IsTruncated><Marker>page2</Marker>
            </ListUsersResult></ListUsersResponse>"#;
            (200, "application/xml", xml.into())
        } else if req.target.contains("Action=ListUsers") && req.target.contains("Marker=page2") {
            let xml = r#"<ListUsersResponse><ListUsersResult>
                <Users><member><UserId>AIDA2</UserId><UserName>bob</UserName><Arn>arn:aws:iam::111:user/bob</Arn><Path>/</Path></member></Users>
                <IsTruncated>false</IsTruncated>
            </ListUsersResult></ListUsersResponse>"#;
            (200, "application/xml", xml.into())
        } else if req.target.contains("Action=ListGroupsForUser") && req.target.contains("UserName=alice") {
            let xml = r#"<ListGroupsForUserResponse><ListGroupsForUserResult>
                <Groups><member><GroupId>AGPA1</GroupId><GroupName>admins</GroupName><Arn>arn:aws:iam::111:group/admins</Arn></member></Groups>
                <IsTruncated>false</IsTruncated>
            </ListGroupsForUserResult></ListGroupsForUserResponse>"#;
            (200, "application/xml", xml.into())
        } else if req.target.contains("Action=ListGroupsForUser") && req.target.contains("UserName=bob") {
            let xml = r#"<ListGroupsForUserResponse><ListGroupsForUserResult>
                <Groups/>
                <IsTruncated>false</IsTruncated>
            </ListGroupsForUserResult></ListGroupsForUserResponse>"#;
            (200, "application/xml", xml.into())
        } else if req.target.contains("Action=ListGroups") {
            let xml = r#"<ListGroupsResponse><ListGroupsResult>
                <Groups><member><GroupId>AGPA1</GroupId><GroupName>admins</GroupName><Arn>arn:aws:iam::111:group/admins</Arn><Path>/</Path></member></Groups>
                <IsTruncated>false</IsTruncated>
            </ListGroupsResult></ListGroupsResponse>"#;
            (200, "application/xml", xml.into())
        } else if req.target.contains("Action=ListRoles") {
            let xml = r#"<ListRolesResponse><ListRolesResult>
                <Roles><member><RoleId>AROA1</RoleId><RoleName>ElderDiscoveryRole</RoleName><Arn>arn:aws:iam::111:role/ElderDiscoveryRole</Arn><Path>/</Path></member></Roles>
                <IsTruncated>false</IsTruncated>
            </ListRolesResult></ListRolesResponse>"#;
            (200, "application/xml", xml.into())
        } else {
            (404, "application/xml", "<Error/>".into())
        }
    })
    .await;

    let elder = test_elder_client(elder_url);
    let provider = json!({
        "id": 301,
        "provider_type": "aws_iam",
        "config_json": {
            "region": "us-east-1",
            "access_key_id": "AKIAEXAMPLEKEY",
            "secret_access_key": "secret-example-key",
            "iam_endpoint_url": iam_url
        }
    });

    let outcome = run_cloud_iam(&elder, &provider).await;
    assert_eq!(outcome.status, "completed");
    assert_eq!(outcome.items_synced, 5);
    assert_eq!(outcome.items_failed, 0);
    assert_eq!(iam_reqs.lock().expect("lock").len(), 6);

    let elder_calls = elder_reqs.lock().expect("lock").clone();
    assert_eq!(elder_calls.len(), 1);
    assert_eq!(
        elder_calls[0].target,
        "/api/v1/integrations/internal/iam/301/apply"
    );
    let apply_body = parse_json_body(&elder_calls[0]);
    assert_eq!(apply_body["success"], true);
    assert!(apply_body["error_message"].is_null());
    assert_eq!(apply_body["users"].as_array().expect("u").len(), 2);
    assert_eq!(apply_body["groups"].as_array().expect("g").len(), 1);
    assert_eq!(apply_body["roles"].as_array().expect("r").len(), 1);
    assert_eq!(apply_body["memberships"].as_array().expect("m").len(), 1);
    assert_eq!(apply_body["memberships"][0]["username"], "alice");
    assert_eq!(apply_body["memberships"][0]["group_name"], "admins");
}

#[tokio::test]
async fn gcp_iam_sync_end_to_end_crm_policy_and_service_accounts() {
    let (priv_pem, _) = ephemeral_rsa_keypair();
    let (elder_url, elder_reqs) = spawn_mock_server(elder_iam_handler(302)).await;
    let (gcp_url, gcp_reqs) = spawn_mock_server(|req| {
        if req.method == "POST" && req.target == "/token" {
            return (
                200,
                "application/json",
                json!({ "access_token": "gcp-iam-tok" }).to_string(),
            );
        }
        if req.headers.get("authorization").map(String::as_str) != Some("Bearer gcp-iam-tok") {
            return (401, "application/json", json!({}).to_string());
        }

        if req.method == "GET" && req.target == "/v1/projects/elder-proj/serviceAccounts" {
            (
                200,
                "application/json",
                json!({
                    "accounts": [{
                        "name": "projects/elder-proj/serviceAccounts/worker@elder-proj.iam.gserviceaccount.com",
                        "uniqueId": "112233",
                        "email": "worker@elder-proj.iam.gserviceaccount.com",
                        "displayName": "Elder Worker",
                        "disabled": false
                    }]
                })
                .to_string(),
            )
        } else if req.method == "POST" && req.target == "/v1/projects/elder-proj:getIamPolicy" {
            (
                200,
                "application/json",
                json!({
                    "version": 1,
                    "bindings": [
                        {
                            "role": "roles/viewer",
                            "members": [
                                "serviceAccount:worker@elder-proj.iam.gserviceaccount.com",
                                "group:secops@example.com"
                            ]
                        },
                        {
                            "role": "roles/editor",
                            "members": ["user:lead@example.com"]
                        }
                    ]
                })
                .to_string(),
            )
        } else {
            (404, "application/json", json!({}).to_string())
        }
    })
    .await;

    let token_uri = format!("{gcp_url}/token");
    let elder = test_elder_client(elder_url);
    let provider = json!({
        "id": 302,
        "provider_type": "gcp_iam",
        "config_json": {
            "project_id": "elder-proj",
            "api_base_url": gcp_url,
            "credentials_json": {
                "client_email": "iam-sync@elder-proj.iam.gserviceaccount.com",
                "private_key": priv_pem,
                "token_uri": token_uri
            }
        }
    });

    let outcome = run_cloud_iam(&elder, &provider).await;
    assert_eq!(outcome.status, "completed");
    assert_eq!(outcome.items_synced, 7);
    assert_eq!(outcome.items_failed, 0);
    assert_eq!(gcp_reqs.lock().expect("lock").len(), 3);

    let apply = parse_json_body(&elder_reqs.lock().expect("lock")[0]);
    assert_eq!(apply["success"], true);
    assert_eq!(apply["users"].as_array().expect("u").len(), 1);
    assert_eq!(apply["groups"].as_array().expect("g").len(), 1);
    assert_eq!(apply["roles"].as_array().expect("r").len(), 2);
    assert_eq!(apply["memberships"].as_array().expect("m").len(), 3);
}

#[tokio::test]
async fn k8s_iam_sync_end_to_end_all_5_rbac_endpoints() {
    let (elder_url, elder_reqs) = spawn_mock_server(elder_iam_handler(303)).await;
    let (k8s_url, k8s_reqs) = spawn_mock_server(|req| {
        if req.headers.get("authorization").map(String::as_str) != Some("Bearer k8s-rbac-tok") {
            return (401, "application/json", json!({}).to_string());
        }
        match req.target.as_str() {
            "/api/v1/serviceaccounts" => (
                200,
                "application/json",
                json!({
                    "items": [{
                        "metadata": { "uid": "sa-uid-1", "name": "elder-agent", "namespace": "kube-system" }
                    }]
                })
                .to_string(),
            ),
            "/apis/rbac.authorization.k8s.io/v1/clusterroles" => (
                200,
                "application/json",
                json!({
                    "items": [{
                        "metadata": { "uid": "cr-uid-1", "name": "view" },
                        "rules": [{ "apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list"] }]
                    }]
                })
                .to_string(),
            ),
            "/apis/rbac.authorization.k8s.io/v1/roles" => (
                200,
                "application/json",
                json!({
                    "items": [{
                        "metadata": { "uid": "role-uid-1", "name": "ns-reader", "namespace": "prod" },
                        "rules": [{ "apiGroups": [""], "resources": ["services"], "verbs": ["get"] }]
                    }]
                })
                .to_string(),
            ),
            "/apis/rbac.authorization.k8s.io/v1/clusterrolebindings" => (
                200,
                "application/json",
                json!({
                    "items": [{
                        "metadata": { "uid": "crb-uid-1", "name": "elder-view-binding" },
                        "roleRef": { "kind": "ClusterRole", "name": "view" },
                        "subjects": [
                            { "kind": "ServiceAccount", "name": "elder-agent", "namespace": "kube-system" },
                            { "kind": "Group", "name": "platform-admins" }
                        ]
                    }]
                })
                .to_string(),
            ),
            "/apis/rbac.authorization.k8s.io/v1/rolebindings" => (
                200,
                "application/json",
                json!({
                    "items": [{
                        "metadata": { "uid": "rb-uid-1", "name": "prod-reader-binding", "namespace": "prod" },
                        "roleRef": { "kind": "Role", "name": "ns-reader" },
                        "subjects": [
                            { "kind": "ServiceAccount", "name": "elder-agent", "namespace": "kube-system" }
                        ]
                    }]
                })
                .to_string(),
            ),
            _ => (404, "application/json", json!({}).to_string()),
        }
    })
    .await;

    let elder = test_elder_client(elder_url);
    let provider = json!({
        "id": 303,
        "provider_type": "kubernetes",
        "config_json": {
            "api_server": k8s_url,
            "token": "k8s-rbac-tok"
        }
    });

    let outcome = run_cloud_iam(&elder, &provider).await;
    assert_eq!(outcome.status, "completed");
    assert_eq!(outcome.items_synced, 7);
    assert_eq!(outcome.items_failed, 0);
    assert_eq!(k8s_reqs.lock().expect("lock").len(), 5);

    let apply = parse_json_body(&elder_reqs.lock().expect("lock")[0]);
    assert_eq!(apply["success"], true);
    assert_eq!(apply["users"].as_array().expect("u").len(), 1);
    assert_eq!(apply["groups"].as_array().expect("g").len(), 1);
    assert_eq!(apply["roles"].as_array().expect("r").len(), 2);
    assert_eq!(apply["memberships"].as_array().expect("m").len(), 3);
}

#[tokio::test]
async fn unsupported_provider_and_config_failure_report_failed_to_complete_and_apply() {
    let (elder_url, elder_reqs) = spawn_mock_server(|req| {
        if req.target.ends_with("/start") {
            (
                200,
                "application/json",
                json!({ "history_id": 777 }).to_string(),
            )
        } else {
            (200, "application/json", json!({ "ok": true }).to_string())
        }
    })
    .await;

    let elder = test_elder_client(elder_url);

    let disc_outcome = run_cloud_discovery(
        &elder,
        &json!({ "id": 201, "provider": "oracle_cloud", "config_json": {} }),
    )
    .await;
    assert_eq!(disc_outcome.status, "failed");
    assert_eq!(disc_outcome.items_failed, 1);

    let iam_outcome = run_cloud_iam(
        &elder,
        &json!({ "id": 401, "provider_type": "unknown_iam", "config_json": {} }),
    )
    .await;
    assert_eq!(iam_outcome.status, "failed");
    assert_eq!(iam_outcome.items_failed, 1);

    let calls = elder_reqs.lock().expect("lock").clone();
    assert_eq!(calls.len(), 3);
    assert_eq!(
        calls[0].target,
        "/api/v1/integrations/internal/discovery/201/start"
    );
    assert_eq!(
        calls[1].target,
        "/api/v1/integrations/internal/discovery/201/complete"
    );
    let complete = parse_json_body(&calls[1]);
    assert_eq!(complete["status"], "failed");
    assert_eq!(complete["history_id"], 777);

    assert_eq!(
        calls[2].target,
        "/api/v1/integrations/internal/iam/401/apply"
    );
    let apply = parse_json_body(&calls[2]);
    assert_eq!(apply["success"], false);
}

#[tokio::test]
async fn discovery_start_failure_returns_failed_outcome_without_complete() {
    let (elder_url, elder_reqs) = spawn_mock_server(|_req| {
        (
            500,
            "application/json",
            json!({ "error": "db down" }).to_string(),
        )
    })
    .await;

    let elder = test_elder_client(elder_url);
    let outcome = run_cloud_discovery(
        &elder,
        &json!({ "id": 202, "provider": "vultr", "config_json": { "api_key": "k" } }),
    )
    .await;

    assert_eq!(outcome.status, "failed");
    assert_eq!(outcome.items_synced, 0);
    assert_eq!(outcome.items_failed, 1);
    assert!(outcome.error.as_deref().unwrap_or("").contains("500"));
    assert_eq!(elder_reqs.lock().expect("lock").len(), 1);
}

#[tokio::test]
async fn missing_job_or_provider_id_fails_cleanly_without_network_calls() {
    let (elder_url, elder_reqs) =
        spawn_mock_server(|_req| (200, "application/json", "{}".into())).await;
    let elder = test_elder_client(elder_url);

    let d = run_cloud_discovery(&elder, &json!({ "provider": "aws" })).await;
    assert_eq!(d.status, "failed");
    assert_eq!(d.items_failed, 1);

    let i = run_cloud_iam(&elder, &json!({ "provider_type": "aws_iam" })).await;
    assert_eq!(i.status, "failed");
    assert_eq!(i.items_failed, 1);

    assert!(elder_reqs.lock().expect("lock").is_empty());
}
