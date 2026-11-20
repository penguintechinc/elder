//! AWS cloud discovery (EC2, VPC, Subnet, S3, RDS) and AWS IAM sync via pure-Rust SigV4.

use chrono::Utc;
use reqwest::Client;
use serde_json::{json, Map, Value};

use crate::common::{
    build_http_client, normalize_http_base_url, opt_bool, opt_str, opt_string_set,
    parse_aws_tag_set, safe_endpoint_label, truncate_body, xml_child, xml_child_text,
    xml_descendant, xml_descendant_text, CloudError, CloudResult, DiscoveryCollection,
    IamCollection, NormalizedResource, TlsOptions,
};
use crate::sigv4::{sign_request, AwsCredentials, SigV4Input};

const EC2_API_VERSION: &str = "2016-11-15";
const RDS_API_VERSION: &str = "2014-10-31";
const IAM_API_VERSION: &str = "2010-05-08";

#[derive(Debug, Clone)]
struct AwsConfig {
    region: String,
    creds: AwsCredentials,
    ec2_endpoint: String,
    s3_endpoint: String,
    rds_endpoint: String,
    iam_endpoint: String,
    services: std::collections::BTreeSet<String>,
    tls: TlsOptions,
}

impl AwsConfig {
    fn from_map(cfg: &Map<String, Value>) -> CloudResult<Self> {
        let region = opt_str(cfg, &["region", "aws_region"])
            .or_else(|| non_empty_env("AWS_DEFAULT_REGION"))
            .unwrap_or_else(|| "us-east-1".to_string());

        let access_key_id = opt_str(cfg, &["access_key_id", "aws_access_key_id"])
            .or_else(|| non_empty_env("AWS_ACCESS_KEY_ID"))
            .ok_or_else(|| CloudError::Config("missing AWS access_key_id".into()))?;
        let secret_access_key = opt_str(cfg, &["secret_access_key", "aws_secret_access_key"])
            .or_else(|| non_empty_env("AWS_SECRET_ACCESS_KEY"))
            .ok_or_else(|| CloudError::Config("missing AWS secret_access_key".into()))?;
        let session_token = opt_str(cfg, &["session_token", "aws_session_token"])
            .or_else(|| non_empty_env("AWS_SESSION_TOKEN"));

        let global_endpoint = opt_str(cfg, &["endpoint_url"]);
        let ec2_endpoint = resolve_service_endpoint(
            cfg,
            "ec2_endpoint_url",
            "ec2",
            global_endpoint.as_deref(),
            &format!("https://ec2.{region}.amazonaws.com"),
        )?;
        let s3_endpoint = resolve_service_endpoint(
            cfg,
            "s3_endpoint_url",
            "s3",
            global_endpoint.as_deref(),
            &format!("https://s3.{region}.amazonaws.com"),
        )?;
        let rds_endpoint = resolve_service_endpoint(
            cfg,
            "rds_endpoint_url",
            "rds",
            global_endpoint.as_deref(),
            &format!("https://rds.{region}.amazonaws.com"),
        )?;
        let iam_endpoint = resolve_service_endpoint(
            cfg,
            "iam_endpoint_url",
            "iam",
            global_endpoint.as_deref(),
            "https://iam.amazonaws.com",
        )?;

        let services = opt_string_set(cfg, "services");
        let tls = TlsOptions {
            verify_ssl: opt_bool(cfg, &["verify_ssl"], true),
            ca_cert_pem: opt_str(cfg, &["ca_cert_pem", "ca_cert"]),
            identity_pem: None,
        };

        Ok(Self {
            region,
            creds: AwsCredentials {
                access_key_id,
                secret_access_key,
                session_token,
            },
            ec2_endpoint,
            s3_endpoint,
            rds_endpoint,
            iam_endpoint,
            services,
            tls,
        })
    }

    fn wants_service(&self, candidates: &[&str]) -> bool {
        if self.services.is_empty() {
            return true;
        }
        candidates.iter().any(|c| self.services.contains(*c))
    }
}

fn non_empty_env(name: &str) -> Option<String> {
    std::env::var(name)
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
}

fn resolve_service_endpoint(
    cfg: &Map<String, Value>,
    flat_key: &str,
    nested_key: &str,
    global_endpoint: Option<&str>,
    default_url: &str,
) -> CloudResult<String> {
    let nested = cfg
        .get("endpoints")
        .and_then(Value::as_object)
        .and_then(|m| m.get(nested_key))
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(ToOwned::to_owned);

    let chosen = opt_str(cfg, &[flat_key])
        .or(nested)
        .or_else(|| global_endpoint.map(ToOwned::to_owned))
        .unwrap_or_else(|| default_url.to_string());

    normalize_http_base_url(&chosen, flat_key)
}

async fn signed_get(
    http: &Client,
    aws: &AwsConfig,
    service: &str,
    base_url: &str,
    query_params: &[(&str, &str)],
) -> CloudResult<String> {
    let mut parsed = url::Url::parse(base_url)
        .map_err(|e| CloudError::Config(format!("invalid AWS endpoint URL: {e}")))?;
    if !query_params.is_empty() {
        let mut pairs = parsed.query_pairs_mut();
        for (k, v) in query_params {
            pairs.append_pair(k, v);
        }
    }

    let signed = sign_request(
        &aws.creds,
        &SigV4Input {
            method: "GET",
            url: &parsed,
            region: &aws.region,
            service,
            payload: b"",
            extra_headers: &[],
            include_payload_hash_header: service == "s3",
            timestamp: Utc::now(),
        },
    )?;

    let headers = signed.to_header_map()?;
    let resp = http.get(parsed.clone()).headers(headers).send().await?;
    let status = resp.status();
    let body = resp.text().await.unwrap_or_default();
    if !status.is_success() {
        return Err(CloudError::Http {
            status: status.as_u16(),
            endpoint: safe_endpoint_label(parsed.as_str()),
            body: truncate_body(&body),
        });
    }
    Ok(body)
}

/// Run AWS cloud discovery (EC2 instances, VPCs, subnets, S3 buckets, RDS instances).
pub async fn discover(cfg_map: &Map<String, Value>) -> CloudResult<DiscoveryCollection> {
    let cfg = AwsConfig::from_map(cfg_map)?;
    let http = build_http_client(&cfg.tls)?;
    let mut out = DiscoveryCollection::default();

    if cfg.wants_service(&["ec2", "compute"]) {
        match fetch_ec2_instances(&http, &cfg, &mut out).await {
            Ok(items) => {
                out.mark_step_ok();
                out.resources.extend(items);
            }
            Err(err) => out.mark_step_err(format!("EC2 DescribeInstances: {err}")),
        }
    }

    if cfg.wants_service(&["vpc", "ec2", "network"]) {
        match fetch_vpcs(&http, &cfg).await {
            Ok(items) => {
                out.mark_step_ok();
                out.resources.extend(items);
            }
            Err(err) => out.mark_step_err(format!("EC2 DescribeVpcs: {err}")),
        }

        match fetch_subnets(&http, &cfg, &mut out).await {
            Ok(items) => {
                out.mark_step_ok();
                out.resources.extend(items);
            }
            Err(err) => out.mark_step_err(format!("EC2 DescribeSubnets: {err}")),
        }
    }

    if cfg.wants_service(&["s3", "storage"]) {
        match fetch_s3_buckets(&http, &cfg).await {
            Ok(items) => {
                out.mark_step_ok();
                out.resources.extend(items);
            }
            Err(err) => out.mark_step_err(format!("S3 ListBuckets: {err}")),
        }
    }

    if cfg.wants_service(&["rds", "database"]) {
        match fetch_rds_instances(&http, &cfg, &mut out).await {
            Ok(items) => {
                out.mark_step_ok();
                out.resources.extend(items);
            }
            Err(err) => out.mark_step_err(format!("RDS DescribeDBInstances: {err}")),
        }
    }

    Ok(out)
}

async fn fetch_ec2_instances(
    http: &Client,
    cfg: &AwsConfig,
    out: &mut DiscoveryCollection,
) -> CloudResult<Vec<NormalizedResource>> {
    let mut resources = Vec::new();
    let mut next_token: Option<String> = None;

    loop {
        let mut params = vec![
            ("Action", "DescribeInstances"),
            ("Version", EC2_API_VERSION),
        ];
        if let Some(tok) = next_token.as_deref() {
            params.push(("NextToken", tok));
        }

        let xml_body = signed_get(http, cfg, "ec2", &cfg.ec2_endpoint, &params).await?;
        let doc = roxmltree::Document::parse(&xml_body)
            .map_err(|e| CloudError::Parse(format!("invalid EC2 DescribeInstances XML: {e}")))?;

        let root = doc.root_element();
        if let Some(res_set) = xml_descendant(root, "reservationSet") {
            for res_item in res_set
                .children()
                .filter(|c| c.is_element() && c.tag_name().name() == "item")
            {
                if let Some(inst_set) = xml_child(res_item, "instancesSet") {
                    for inst in inst_set
                        .children()
                        .filter(|c| c.is_element() && c.tag_name().name() == "item")
                    {
                        if let Some(parsed) = parse_ec2_instance(inst, &cfg.region, out) {
                            resources.push(parsed);
                        }
                    }
                }
            }
        }

        next_token = xml_child_text(root, "nextToken");
        if next_token.is_none() {
            break;
        }
    }

    Ok(resources)
}

fn parse_ec2_instance(
    inst: roxmltree::Node<'_, '_>,
    region: &str,
    out: &mut DiscoveryCollection,
) -> Option<NormalizedResource> {
    let instance_id = xml_child_text(inst, "instanceId")?;
    let state = xml_child(inst, "instanceState").and_then(|s| xml_child_text(s, "name"));
    if state.as_deref() == Some("terminated") {
        return None;
    }

    let tags = parse_aws_tag_set(inst);
    let name = tags
        .get("Name")
        .filter(|s| !s.is_empty())
        .cloned()
        .unwrap_or_else(|| instance_id.clone());

    let instance_type = xml_child_text(inst, "instanceType");
    let private_ip = xml_child_text(inst, "privateIpAddress");
    let public_ip = xml_child_text(inst, "ipAddress");
    let vpc_id = xml_child_text(inst, "vpcId");
    let subnet_id = xml_child_text(inst, "subnetId");
    let launch_time = xml_child_text(inst, "launchTime");
    let az = xml_child(inst, "placement").and_then(|p| xml_child_text(p, "availabilityZone"));

    let mut res = NormalizedResource::new(
        &instance_id,
        &instance_id,
        "ec2_instance",
        "compute",
        name,
        "aws",
    );
    res.region = Some(region.to_string());
    res.tags = tags;
    res.metadata = json!({
        "instance_type": instance_type,
        "state": state,
        "private_ip": private_ip,
        "public_ip": public_ip,
        "vpc_id": vpc_id,
        "subnet_id": subnet_id,
        "availability_zone": az,
        "launch_time": launch_time,
    });

    if let Some(vpc) = &vpc_id {
        res.add_relationship(vpc, "networking_resource", "in_network");
        out.push_dependency(
            &instance_id,
            "entity",
            vpc,
            "networking_resource",
            "in_network",
        );
    }
    if let Some(subnet) = &subnet_id {
        res.add_relationship(subnet, "networking_resource", "in_subnet");
        out.push_dependency(
            &instance_id,
            "entity",
            subnet,
            "networking_resource",
            "in_subnet",
        );
    }
    if let Some(group_set) = xml_child(inst, "groupSet") {
        for sg in group_set
            .children()
            .filter(|c| c.is_element() && c.tag_name().name() == "item")
        {
            if let Some(sg_id) = xml_child_text(sg, "groupId") {
                res.add_relationship(&sg_id, "networking_resource", "uses_security_group");
                out.push_dependency(
                    &instance_id,
                    "entity",
                    &sg_id,
                    "networking_resource",
                    "uses_security_group",
                );
            }
        }
    }
    if let Some(profile_arn) =
        xml_child(inst, "iamInstanceProfile").and_then(|p| xml_child_text(p, "arn"))
    {
        res.add_relationship(&profile_arn, "identity", "assumes_role");
        out.push_dependency(
            &instance_id,
            "entity",
            &profile_arn,
            "identity",
            "assumes_role",
        );
    }

    Some(res)
}

async fn fetch_vpcs(http: &Client, cfg: &AwsConfig) -> CloudResult<Vec<NormalizedResource>> {
    let mut resources = Vec::new();
    let mut next_token: Option<String> = None;

    loop {
        let mut params = vec![("Action", "DescribeVpcs"), ("Version", EC2_API_VERSION)];
        if let Some(tok) = next_token.as_deref() {
            params.push(("NextToken", tok));
        }

        let xml_body = signed_get(http, cfg, "ec2", &cfg.ec2_endpoint, &params).await?;
        let doc = roxmltree::Document::parse(&xml_body)
            .map_err(|e| CloudError::Parse(format!("invalid EC2 DescribeVpcs XML: {e}")))?;

        let root = doc.root_element();
        if let Some(vpc_set) = xml_descendant(root, "vpcSet") {
            for vpc in vpc_set
                .children()
                .filter(|c| c.is_element() && c.tag_name().name() == "item")
            {
                let Some(vpc_id) = xml_child_text(vpc, "vpcId") else {
                    continue;
                };
                let tags = parse_aws_tag_set(vpc);
                let name = tags
                    .get("Name")
                    .filter(|s| !s.is_empty())
                    .cloned()
                    .unwrap_or_else(|| vpc_id.clone());
                let cidr_block = xml_child_text(vpc, "cidrBlock");
                let state = xml_child_text(vpc, "state");
                let is_default = xml_child_text(vpc, "isDefault").map(|s| s == "true");

                let mut res =
                    NormalizedResource::new(&vpc_id, &vpc_id, "vpc", "network", name, "aws");
                res.region = Some(cfg.region.clone());
                res.tags = tags;
                res.metadata = json!({
                    "vpc_id": vpc_id,
                    "cidr_block": cidr_block,
                    "state": state,
                    "is_default": is_default,
                });
                resources.push(res);
            }
        }

        next_token = xml_child_text(root, "nextToken");
        if next_token.is_none() {
            break;
        }
    }

    Ok(resources)
}

async fn fetch_subnets(
    http: &Client,
    cfg: &AwsConfig,
    out: &mut DiscoveryCollection,
) -> CloudResult<Vec<NormalizedResource>> {
    let mut resources = Vec::new();
    let mut next_token: Option<String> = None;

    loop {
        let mut params = vec![("Action", "DescribeSubnets"), ("Version", EC2_API_VERSION)];
        if let Some(tok) = next_token.as_deref() {
            params.push(("NextToken", tok));
        }

        let xml_body = signed_get(http, cfg, "ec2", &cfg.ec2_endpoint, &params).await?;
        let doc = roxmltree::Document::parse(&xml_body)
            .map_err(|e| CloudError::Parse(format!("invalid EC2 DescribeSubnets XML: {e}")))?;

        let root = doc.root_element();
        if let Some(subnet_set) = xml_descendant(root, "subnetSet") {
            for subnet in subnet_set
                .children()
                .filter(|c| c.is_element() && c.tag_name().name() == "item")
            {
                let Some(subnet_id) = xml_child_text(subnet, "subnetId") else {
                    continue;
                };
                let tags = parse_aws_tag_set(subnet);
                let name = tags
                    .get("Name")
                    .filter(|s| !s.is_empty())
                    .cloned()
                    .unwrap_or_else(|| subnet_id.clone());
                let vpc_id = xml_child_text(subnet, "vpcId");
                let cidr_block = xml_child_text(subnet, "cidrBlock");
                let az = xml_child_text(subnet, "availabilityZone");
                let available_ips = xml_child_text(subnet, "availableIpAddressCount")
                    .and_then(|s| s.parse::<u64>().ok());

                let mut res = NormalizedResource::new(
                    &subnet_id, &subnet_id, "subnet", "network", name, "aws",
                );
                res.region = Some(cfg.region.clone());
                res.tags = tags;
                res.metadata = json!({
                    "subnet_id": subnet_id,
                    "vpc_id": vpc_id,
                    "cidr_block": cidr_block,
                    "availability_zone": az,
                    "available_ip_addresses": available_ips,
                });

                if let Some(vpc) = &vpc_id {
                    res.add_relationship(vpc, "networking_resource", "in_network");
                    out.push_dependency(
                        &subnet_id,
                        "networking_resource",
                        vpc,
                        "networking_resource",
                        "in_network",
                    );
                }

                resources.push(res);
            }
        }

        next_token = xml_child_text(root, "nextToken");
        if next_token.is_none() {
            break;
        }
    }

    Ok(resources)
}

async fn fetch_s3_buckets(http: &Client, cfg: &AwsConfig) -> CloudResult<Vec<NormalizedResource>> {
    let xml_body = signed_get(http, cfg, "s3", &cfg.s3_endpoint, &[]).await?;
    let doc = roxmltree::Document::parse(&xml_body)
        .map_err(|e| CloudError::Parse(format!("invalid S3 ListBuckets XML: {e}")))?;

    let mut resources = Vec::new();
    let root = doc.root_element();
    if let Some(buckets) = xml_descendant(root, "Buckets") {
        for bucket in buckets
            .children()
            .filter(|c| c.is_element() && c.tag_name().name() == "Bucket")
        {
            let Some(name) = xml_child_text(bucket, "Name") else {
                continue;
            };
            let creation_date = xml_child_text(bucket, "CreationDate");
            let mut res =
                NormalizedResource::new(&name, &name, "s3_bucket", "storage", &name, "aws");
            res.region = Some(cfg.region.clone());
            res.metadata = json!({
                "creation_date": creation_date,
                "bucket_arn": format!("arn:aws:s3:::{name}"),
            });
            resources.push(res);
        }
    }

    Ok(resources)
}

async fn fetch_rds_instances(
    http: &Client,
    cfg: &AwsConfig,
    out: &mut DiscoveryCollection,
) -> CloudResult<Vec<NormalizedResource>> {
    let mut resources = Vec::new();
    let mut marker: Option<String> = None;

    loop {
        let mut params = vec![
            ("Action", "DescribeDBInstances"),
            ("Version", RDS_API_VERSION),
        ];
        if let Some(m) = marker.as_deref() {
            params.push(("Marker", m));
        }

        let xml_body = signed_get(http, cfg, "rds", &cfg.rds_endpoint, &params).await?;
        let doc = roxmltree::Document::parse(&xml_body)
            .map_err(|e| CloudError::Parse(format!("invalid RDS DescribeDBInstances XML: {e}")))?;

        let root = doc.root_element();
        if let Some(dbs) = xml_descendant(root, "DBInstances") {
            for db in dbs
                .children()
                .filter(|c| c.is_element() && c.tag_name().name() == "DBInstance")
            {
                let Some(db_id) = xml_child_text(db, "DBInstanceIdentifier") else {
                    continue;
                };
                let db_arn = xml_child_text(db, "DBInstanceArn").unwrap_or_else(|| db_id.clone());
                let engine = xml_child_text(db, "Engine");
                let engine_version = xml_child_text(db, "EngineVersion");
                let instance_class = xml_child_text(db, "DBInstanceClass");
                let storage_type = xml_child_text(db, "StorageType");
                let allocated_storage =
                    xml_child_text(db, "AllocatedStorage").and_then(|s| s.parse::<u64>().ok());
                let status = xml_child_text(db, "DBInstanceStatus");
                let multi_az = xml_child_text(db, "MultiAZ").map(|s| s == "true");
                let az = xml_child_text(db, "AvailabilityZone");
                let endpoint_node = xml_child(db, "Endpoint");
                let address = endpoint_node.and_then(|e| xml_child_text(e, "Address"));
                let port = endpoint_node
                    .and_then(|e| xml_child_text(e, "Port"))
                    .and_then(|s| s.parse::<u16>().ok());
                let vpc_id =
                    xml_child(db, "DBSubnetGroup").and_then(|g| xml_child_text(g, "VpcId"));

                let mut res = NormalizedResource::new(
                    &db_id,
                    &db_arn,
                    "rds_instance",
                    "database",
                    &db_id,
                    "aws",
                );
                res.region = Some(cfg.region.clone());
                res.metadata = json!({
                    "engine": engine,
                    "engine_version": engine_version,
                    "instance_class": instance_class,
                    "storage_type": storage_type,
                    "allocated_storage": allocated_storage,
                    "status": status,
                    "endpoint": address,
                    "port": port,
                    "multi_az": multi_az,
                    "availability_zone": az,
                    "vpc_id": vpc_id,
                });

                if let Some(vpc) = &vpc_id {
                    res.add_relationship(vpc, "networking_resource", "in_network");
                    out.push_dependency(
                        &db_arn,
                        "data_store",
                        vpc,
                        "networking_resource",
                        "in_network",
                    );
                }
                if let Some(sgs) = xml_child(db, "VpcSecurityGroups") {
                    for sg in sgs.children().filter(|c| {
                        c.is_element() && c.tag_name().name() == "VpcSecurityGroupMembership"
                    }) {
                        if let Some(sg_id) = xml_child_text(sg, "VpcSecurityGroupId") {
                            res.add_relationship(
                                &sg_id,
                                "networking_resource",
                                "uses_security_group",
                            );
                            out.push_dependency(
                                &db_arn,
                                "data_store",
                                &sg_id,
                                "networking_resource",
                                "uses_security_group",
                            );
                        }
                    }
                }

                resources.push(res);
            }
        }

        marker = xml_descendant_text(root, "Marker");
        if marker.is_none() {
            break;
        }
    }

    Ok(resources)
}

/// Sync AWS IAM users, groups, roles, and group memberships using SigV4.
pub async fn sync_iam(cfg_map: &Map<String, Value>) -> CloudResult<IamCollection> {
    let cfg = AwsConfig::from_map(cfg_map)?;
    let http = build_http_client(&cfg.tls)?;
    let mut out = IamCollection::default();

    let mut usernames: Vec<(String, String)> = Vec::new();
    match fetch_iam_users(&http, &cfg).await {
        Ok(users) => {
            out.mark_step_ok();
            for u in &users {
                if let (Some(name), Some(arn)) = (
                    u.get("username").and_then(Value::as_str),
                    u.get("arn").and_then(Value::as_str),
                ) {
                    usernames.push((name.to_string(), arn.to_string()));
                }
            }
            out.users.extend(users);
        }
        Err(err) => out.mark_step_err(format!("IAM ListUsers: {err}")),
    }

    match fetch_iam_groups(&http, &cfg).await {
        Ok(groups) => {
            out.mark_step_ok();
            out.groups.extend(groups);
        }
        Err(err) => out.mark_step_err(format!("IAM ListGroups: {err}")),
    }

    match fetch_iam_roles(&http, &cfg).await {
        Ok(roles) => {
            out.mark_step_ok();
            out.roles.extend(roles);
        }
        Err(err) => out.mark_step_err(format!("IAM ListRoles: {err}")),
    }

    for (username, user_arn) in usernames {
        match fetch_iam_groups_for_user(&http, &cfg, &username, &user_arn).await {
            Ok(memberships) => {
                out.mark_step_ok();
                out.memberships.extend(memberships);
            }
            Err(err) => out.mark_step_err(format!("IAM ListGroupsForUser: {err}")),
        }
    }

    Ok(out)
}

fn parse_truncated_marker(root: roxmltree::Node<'_, '_>) -> Option<String> {
    let truncated = xml_descendant_text(root, "IsTruncated")
        .map(|s| s.eq_ignore_ascii_case("true"))
        .unwrap_or(false);
    if truncated {
        xml_descendant_text(root, "Marker")
    } else {
        None
    }
}

async fn fetch_iam_users(http: &Client, cfg: &AwsConfig) -> CloudResult<Vec<Value>> {
    let mut users = Vec::new();
    let mut marker: Option<String> = None;

    loop {
        let mut params = vec![("Action", "ListUsers"), ("Version", IAM_API_VERSION)];
        if let Some(m) = marker.as_deref() {
            params.push(("Marker", m));
        }

        let xml_body = signed_get(http, cfg, "iam", &cfg.iam_endpoint, &params).await?;
        let doc = roxmltree::Document::parse(&xml_body)
            .map_err(|e| CloudError::Parse(format!("invalid IAM ListUsers XML: {e}")))?;

        let root = doc.root_element();
        if let Some(users_node) = xml_descendant(root, "Users") {
            for member in users_node
                .children()
                .filter(|c| c.is_element() && c.tag_name().name() == "member")
            {
                let Some(username) = xml_child_text(member, "UserName") else {
                    continue;
                };
                let user_id = xml_child_text(member, "UserId").unwrap_or_else(|| username.clone());
                let arn = xml_child_text(member, "Arn").unwrap_or_else(|| user_id.clone());
                let path = xml_child_text(member, "Path");
                let created_at = xml_child_text(member, "CreateDate");

                users.push(json!({
                    "id": user_id,
                    "external_id": arn,
                    "username": username,
                    "display_name": username,
                    "arn": arn,
                    "path": path,
                    "created_at": created_at,
                    "kind": "iam_user",
                    "provider": "aws_iam",
                }));
            }
        }

        marker = parse_truncated_marker(root);
        if marker.is_none() {
            break;
        }
    }

    Ok(users)
}

async fn fetch_iam_groups(http: &Client, cfg: &AwsConfig) -> CloudResult<Vec<Value>> {
    let mut groups = Vec::new();
    let mut marker: Option<String> = None;

    loop {
        let mut params = vec![("Action", "ListGroups"), ("Version", IAM_API_VERSION)];
        if let Some(m) = marker.as_deref() {
            params.push(("Marker", m));
        }

        let xml_body = signed_get(http, cfg, "iam", &cfg.iam_endpoint, &params).await?;
        let doc = roxmltree::Document::parse(&xml_body)
            .map_err(|e| CloudError::Parse(format!("invalid IAM ListGroups XML: {e}")))?;

        let root = doc.root_element();
        if let Some(groups_node) = xml_descendant(root, "Groups") {
            for member in groups_node
                .children()
                .filter(|c| c.is_element() && c.tag_name().name() == "member")
            {
                let Some(name) = xml_child_text(member, "GroupName") else {
                    continue;
                };
                let group_id = xml_child_text(member, "GroupId").unwrap_or_else(|| name.clone());
                let arn = xml_child_text(member, "Arn").unwrap_or_else(|| group_id.clone());
                let path = xml_child_text(member, "Path");
                let created_at = xml_child_text(member, "CreateDate");

                groups.push(json!({
                    "id": group_id,
                    "external_id": arn,
                    "name": name,
                    "arn": arn,
                    "path": path,
                    "created_at": created_at,
                    "provider": "aws_iam",
                }));
            }
        }

        marker = parse_truncated_marker(root);
        if marker.is_none() {
            break;
        }
    }

    Ok(groups)
}

async fn fetch_iam_roles(http: &Client, cfg: &AwsConfig) -> CloudResult<Vec<Value>> {
    let mut roles = Vec::new();
    let mut marker: Option<String> = None;

    loop {
        let mut params = vec![("Action", "ListRoles"), ("Version", IAM_API_VERSION)];
        if let Some(m) = marker.as_deref() {
            params.push(("Marker", m));
        }

        let xml_body = signed_get(http, cfg, "iam", &cfg.iam_endpoint, &params).await?;
        let doc = roxmltree::Document::parse(&xml_body)
            .map_err(|e| CloudError::Parse(format!("invalid IAM ListRoles XML: {e}")))?;

        let root = doc.root_element();
        if let Some(roles_node) = xml_descendant(root, "Roles") {
            for member in roles_node
                .children()
                .filter(|c| c.is_element() && c.tag_name().name() == "member")
            {
                let Some(name) = xml_child_text(member, "RoleName") else {
                    continue;
                };
                let role_id = xml_child_text(member, "RoleId").unwrap_or_else(|| name.clone());
                let arn = xml_child_text(member, "Arn").unwrap_or_else(|| role_id.clone());
                let path = xml_child_text(member, "Path");
                let description = xml_child_text(member, "Description");
                let created_at = xml_child_text(member, "CreateDate");

                roles.push(json!({
                    "id": role_id,
                    "external_id": arn,
                    "name": name,
                    "arn": arn,
                    "path": path,
                    "description": description,
                    "created_at": created_at,
                    "provider": "aws_iam",
                }));
            }
        }

        marker = parse_truncated_marker(root);
        if marker.is_none() {
            break;
        }
    }

    Ok(roles)
}

async fn fetch_iam_groups_for_user(
    http: &Client,
    cfg: &AwsConfig,
    username: &str,
    user_arn: &str,
) -> CloudResult<Vec<Value>> {
    let mut memberships = Vec::new();
    let mut marker: Option<String> = None;

    loop {
        let mut params = vec![
            ("Action", "ListGroupsForUser"),
            ("UserName", username),
            ("Version", IAM_API_VERSION),
        ];
        if let Some(m) = marker.as_deref() {
            params.push(("Marker", m));
        }

        let xml_body = signed_get(http, cfg, "iam", &cfg.iam_endpoint, &params).await?;
        let doc = roxmltree::Document::parse(&xml_body)
            .map_err(|e| CloudError::Parse(format!("invalid IAM ListGroupsForUser XML: {e}")))?;

        let root = doc.root_element();
        if let Some(groups_node) = xml_descendant(root, "Groups") {
            for member in groups_node
                .children()
                .filter(|c| c.is_element() && c.tag_name().name() == "member")
            {
                let Some(group_name) = xml_child_text(member, "GroupName") else {
                    continue;
                };
                let group_id = xml_child_text(member, "GroupId");
                let group_arn = xml_child_text(member, "Arn")
                    .or(group_id.clone())
                    .unwrap_or_else(|| group_name.clone());

                memberships.push(json!({
                    "user_external_id": user_arn,
                    "username": username,
                    "group_external_id": group_arn,
                    "group_name": group_name,
                    "kind": "group_membership",
                    "provider": "aws_iam",
                }));
            }
        }

        marker = parse_truncated_marker(root);
        if marker.is_none() {
            break;
        }
    }

    Ok(memberships)
}
