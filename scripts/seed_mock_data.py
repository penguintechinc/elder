#!/usr/bin/env python3
"""Mock data seeding script for Elder local development.

This script populates the Elder application with realistic mock data
for local development and testing purposes.

Usage:
    ./scripts/seed_mock_data.py [OPTIONS]

Options:
    --base-url URL      API base URL (default: http://localhost:4000)
    --tenant-id ID      Tenant ID for authentication (default: 1)
    --email EMAIL       Admin email (default: admin@localhost)
    --password PASS     Admin password (default: admin123)
    --count N           Number of items per type (default: 10)
    --verbose, -v       Show detailed progress
    --dry-run           Show what would be created without making requests

Example:
    ./scripts/seed_mock_data.py --base-url http://localhost:4000 --count 5 -v
"""

import argparse
import random
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from faker import Faker

# Entity type constants (matching apps/api/models/entity_types.py)
ENTITY_TYPES = {
    "network": ["router", "firewall", "switch", "subnet", "proxy", "vlan"],
    "compute": ["server", "virtual_machine", "kubernetes_cluster", "serverless"],
    "storage": ["database", "caching", "queue_system", "solid_state_disk"],
    "datacenter": ["public_vpc", "private_vpc", "physical"],
    "security": ["vulnerability", "compliance", "config"],
}

# Service deployment methods
DEPLOYMENT_METHODS = ["kubernetes", "docker", "vm", "serverless", "bare_metal"]

# Programming languages
LANGUAGES = ["python", "go", "javascript", "typescript", "java", "rust", "ruby"]

# Software vendors
SOFTWARE_VENDORS = [
    ("Datadog", "Monitoring"),
    ("PagerDuty", "Incident Management"),
    ("Atlassian", "Project Management"),
    ("Slack", "Communication"),
    ("GitHub", "Version Control"),
    ("AWS", "Cloud Infrastructure"),
    ("Cloudflare", "CDN/Security"),
    ("Okta", "Identity Management"),
    ("Snyk", "Security Scanning"),
    ("HashiCorp", "Infrastructure"),
]


class MockDataSeeder:
    """Generates and seeds mock data into Elder via REST API."""

    def __init__(
        self,
        base_url: str,
        tenant_id: int,
        email: str,
        password: str,
        count: int = 10,
        verbose: bool = False,
        dry_run: bool = False,
        host_header: str = "",
        verify_ssl: bool = True,
    ):
        self.base_url = base_url.rstrip("/")
        self.tenant_id = tenant_id
        self.email = email
        self.password = password
        self.count = count
        self.verbose = verbose
        self.dry_run = dry_run
        self.verify_ssl = verify_ssl

        self.session = requests.Session()
        self.session.verify = verify_ssl
        if host_header:
            self.session.headers.update({"Host": host_header})
        self.token: str | None = None
        self.faker = Faker()

        # Track created resource IDs for cross-references
        self.created: dict[str, list[dict[str, Any]]] = {
            "organizations": [],
            "identities": [],
            "identity_groups": [],
            "entities": [],
            "projects": [],
            "milestones": [],
            "issues": [],
            "labels": [],
            "services": [],
            "software": [],
            "ipam_prefixes": [],
            "ipam_vlans": [],
            "ipam_addresses": [],
            "dependencies": [],
            "data_stores": [],
            "secrets": [],
            "secret_providers": [],
            "api_keys": [],
            "certificates": [],
        }

    def log(self, message: Any, force: bool = False) -> None:
        """Print message if verbose mode or forced.

        Avoid logging sensitive information such as passwords, tokens, or secrets
        in clear text by redacting obvious secret-like fields from mappings.
        """
        if not (self.verbose or force):
            return

        # If a mapping is passed, redact common secret-like fields
        if isinstance(message, dict):
            redacted = {}
            sensitive_keys = {
                "password",
                "passwd",
                "secret",
                "token",
                "access_token",
                "refresh_token",
                "api_key",
                "apikey",
                "authorization",
                "auth",
            }
            for k, v in message.items():
                if isinstance(k, str) and k.lower() in sensitive_keys:
                    redacted[k] = "***redacted***"
                else:
                    redacted[k] = v
            print(redacted)
        else:
            print(message)

    def authenticate(self) -> bool:
        """Authenticate and get JWT token."""
        self.log("Authenticating...")

        if self.dry_run:
            self.log("  [DRY RUN] Would authenticate as: " + self.email)
            self.token = "dry-run-token"
            return True

        try:
            response = self.session.post(
                f"{self.base_url}/api/v1/portal-auth/login",
                json={
                    "tenant_id": self.tenant_id,
                    "email": self.email,
                    "password": self.password,
                },
            )

            if response.status_code == 200:
                data = response.json()
                self.token = data.get("access_token") or data.get("token")
                self.session.headers["Authorization"] = f"Bearer {self.token}"
                self.log("  Authenticated successfully")
                return True
            else:
                print(
                    f"  Authentication failed: {response.status_code} - "
                    f"{response.text}",
                    file=sys.stderr,
                )
                return False
        except requests.RequestException as e:
            print(f"  Connection error: {e}", file=sys.stderr)
            return False

    def _api_post(self, endpoint: str, data: dict[str, Any]) -> dict[str, Any] | None:
        """Make authenticated POST request to API."""
        if self.dry_run:
            name = data.get("name", "<no-name>")
            self.log(f"  [DRY RUN] POST {endpoint}: {name}")
            # Return mock response with fake ID
            return {"id": random.randint(1000, 9999), **data}

        try:
            response = self.session.post(
                f"{self.base_url}{endpoint}",
                json=data,
            )

            if response.status_code in (200, 201):
                return response.json()
            else:
                self.log(
                    f"  Failed POST {endpoint}: {response.status_code} - "
                    f"{response.text[:200]}"
                )
                return None
        except requests.RequestException as e:
            self.log(f"  Request error for {endpoint}: {e}")
            return None

    def _api_get(self, endpoint: str) -> dict[str, Any] | None:
        """Make authenticated GET request to API."""
        if self.dry_run:
            return {"items": [], "total": 0}

        try:
            response = self.session.get(f"{self.base_url}{endpoint}")
            if response.status_code == 200:
                return response.json()
            return None
        except requests.RequestException:
            return None

    def seed_organizations(self) -> None:
        """Create organization hierarchy."""
        self.log("\nSeeding Organizations...")

        # Create top-level orgs
        top_level_orgs = [
            ("Engineering", "Engineering department"),
            ("Operations", "IT Operations and Infrastructure"),
            ("Security", "Information Security team"),
            ("Product", "Product Management"),
            ("Finance", "Finance and Accounting"),
        ]

        for name, description in top_level_orgs[: self.count]:
            result = self._api_post(
                "/api/v1/organizations",
                {
                    "name": name,
                    "description": description,
                    "organization_type": "department",
                },
            )
            if result:
                self.created["organizations"].append(result)
                self.log(f"  Created org: {name}")

        # Create sub-orgs under Engineering
        if self.created["organizations"]:
            eng_org = self.created["organizations"][0]
            sub_orgs = ["Backend", "Frontend", "Platform", "QA", "DevOps"]

            for name in sub_orgs[: max(3, self.count // 2)]:
                result = self._api_post(
                    "/api/v1/organizations",
                    {
                        "name": name,
                        "description": f"{name} team under Engineering",
                        "organization_type": "team",
                        "parent_id": eng_org.get("id"),
                    },
                )
                if result:
                    self.created["organizations"].append(result)
                    self.log(f"  Created sub-org: {name}")

    def seed_identities(self) -> None:
        """Create user identities."""
        self.log("\nSeeding Identities...")

        for i in range(self.count):
            first_name = self.faker.first_name()
            last_name = self.faker.last_name()
            username = f"{first_name.lower()}.{last_name.lower()}"

            # identity_type must be 'human' or 'service_account'
            identity_type = random.choice(["human", "human", "service_account"])

            result = self._api_post(
                "/api/v1/identities",
                {
                    "username": username,
                    "email": f"{username}@example.com",
                    "full_name": f"{first_name} {last_name}",
                    "identity_type": identity_type,
                    "auth_provider": "local",
                    "password": "MockPassword123!",  # Required for local auth
                    "organization_id": self._random_org_id(),
                    "is_active": random.random() > 0.1,  # 90% active
                },
            )
            if result:
                self.created["identities"].append(result)
                self.log(f"  Created identity: {username}")

    def seed_identity_groups(self) -> None:
        """Create identity groups."""
        self.log("\nSeeding Identity Groups...")

        groups = [
            ("developers", "Software Developers"),
            ("sre-team", "Site Reliability Engineers"),
            ("security-team", "Security Team"),
            ("platform-team", "Platform Engineering"),
            ("oncall-primary", "Primary On-Call Rotation"),
            ("oncall-secondary", "Secondary On-Call Rotation"),
            ("admins", "System Administrators"),
        ]

        for name, description in groups[: self.count]:
            result = self._api_post(
                "/api/v1/identities/groups",  # Correct endpoint
                {
                    "name": name,
                    "description": description,
                    "is_active": True,
                },
            )
            if result:
                self.created["identity_groups"].append(result)
                self.log(f"  Created group: {name}")

    def seed_entities(self) -> None:
        """Create entities of all types with sub-types."""
        self.log("\nSeeding Entities...")

        # Must have orgs first
        if not self.created["organizations"]:
            self.log("  No organizations - skipping entities")
            return

        for entity_type, sub_types in ENTITY_TYPES.items():
            self.log(f"  Creating {entity_type} entities...")

            for i in range(min(self.count, len(sub_types) * 2)):
                sub_type = random.choice(sub_types)
                name, attributes = self._generate_entity_data(entity_type, sub_type, i)

                result = self._api_post(
                    "/api/v1/entities",
                    {
                        "name": name,
                        "description": f"{entity_type.title()} - {sub_type}",
                        "entity_type": entity_type,
                        "sub_type": sub_type,
                        "organization_id": self._random_org_id(),
                        "attributes": attributes,
                        "tags": self._random_tags(),
                        "is_active": random.random() > 0.05,
                    },
                )
                if result:
                    self.created["entities"].append(result)
                    self.log(f"    Created: {name} ({sub_type})")

    def _generate_entity_data(
        self, entity_type: str, sub_type: str, index: int
    ) -> tuple[str, dict[str, Any]]:
        """Generate realistic entity name and attributes based on type."""
        attributes: dict[str, Any] = {}

        if entity_type == "network":
            if sub_type == "router":
                name = f"router-{random.choice(['core', 'edge', 'dist'])}-{index:02d}"
                attributes = {
                    "routing_protocols": random.sample(
                        ["BGP", "OSPF", "EIGRP", "RIP"], k=random.randint(1, 2)
                    ),
                    "interfaces": random.randint(4, 48),
                }
            elif sub_type == "firewall":
                name = (
                    f"fw-{random.choice(['perimeter', 'internal', 'dmz'])}-{index:02d}"
                )
                attributes = {
                    "default_policy": random.choice(["deny", "allow"]),
                    "rule_count": random.randint(50, 500),
                }
            elif sub_type == "switch":
                name = f"sw-{random.choice(['tor', 'leaf', 'spine'])}-{index:02d}"
                attributes = {
                    "port_count": random.choice([24, 48, 96]),
                    "speed": random.choice(["1G", "10G", "25G", "100G"]),
                }
            elif sub_type == "subnet":
                octet = random.randint(0, 255)
                name = f"subnet-10.{octet}.0.0-24"
                attributes = {
                    "cidr_block": f"10.{octet}.0.0/24",
                    "gateway": f"10.{octet}.0.1",
                    "available_ips": random.randint(100, 250),
                }
            else:
                name = f"{sub_type}-{index:02d}"

        elif entity_type == "compute":
            if sub_type == "server":
                name = f"srv-{random.choice(['web', 'api', 'db', 'app'])}-{index:02d}"
                attributes = {
                    "os": random.choice(
                        ["Ubuntu 22.04", "RHEL 9", "Debian 12", "Rocky Linux 9"]
                    ),
                    "cpu_count": random.choice([4, 8, 16, 32, 64]),
                    "memory_gb": random.choice([8, 16, 32, 64, 128]),
                    "hostname": f"{name}.internal.example.com",
                }
            elif sub_type == "virtual_machine":
                name = f"vm-{self.faker.word()}-{index:02d}"
                attributes = {
                    "os": random.choice(
                        ["Ubuntu 22.04", "Windows Server 2022", "CentOS Stream 9"]
                    ),
                    "vcpu_count": random.choice([2, 4, 8, 16]),
                    "memory_gb": random.choice([4, 8, 16, 32]),
                    "disk_gb": random.choice([50, 100, 200, 500]),
                    "hypervisor": random.choice(["VMware", "KVM", "Hyper-V"]),
                }
            elif sub_type == "kubernetes_cluster":
                name = f"k8s-{random.choice(['prod', 'staging', 'dev'])}-{index:02d}"
                attributes = {
                    "version": f"1.{random.randint(27, 31)}.{random.randint(0, 5)}",
                    "node_count": random.randint(3, 50),
                    "control_plane_endpoint": f"https://{name}.k8s.example.com:6443",
                }
            else:
                name = f"{sub_type}-{index:02d}"

        elif entity_type == "storage":
            if sub_type == "database":
                engine = random.choice(["PostgreSQL", "MySQL", "MongoDB", "Redis"])
                name = f"db-{engine.lower()}-{index:02d}"
                attributes = {
                    "engine": engine,
                    "version": self._random_version(),
                    "port": {"PostgreSQL": 5432, "MySQL": 3306, "MongoDB": 27017}.get(
                        engine, 6379
                    ),
                    "replica_count": random.randint(0, 3),
                }
            elif sub_type == "caching":
                name = f"cache-{random.choice(['redis', 'memcached'])}-{index:02d}"
                attributes = {
                    "engine": random.choice(["Redis", "Valkey", "Memcached"]),
                    "memory_mb": random.choice([512, 1024, 2048, 4096]),
                    "eviction_policy": random.choice(
                        ["allkeys-lru", "volatile-lru", "noeviction"]
                    ),
                }
            elif sub_type == "queue_system":
                name = (
                    f"queue-{random.choice(['kafka', 'rabbitmq', 'sqs'])}-{index:02d}"
                )
                attributes = {
                    "engine": random.choice(["Kafka", "RabbitMQ", "SQS"]),
                    "message_retention_hours": random.choice([24, 72, 168, 336]),
                }
            else:
                name = f"{sub_type}-{index:02d}"

        elif entity_type == "datacenter":
            if sub_type == "public_vpc":
                name = f"vpc-public-{random.choice(['us-east', 'us-west', 'eu-west'])}"
                attributes = {
                    "cidr_block": f"10.{random.randint(0, 255)}.0.0/16",
                    "region": random.choice(
                        ["us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1"]
                    ),
                }
            elif sub_type == "private_vpc":
                name = f"vpc-private-{random.choice(['prod', 'staging', 'dev'])}"
                attributes = {
                    "cidr_block": f"172.{random.randint(16, 31)}.0.0/16",
                    "region": random.choice(["us-east-1", "us-west-2", "eu-west-1"]),
                }
            elif sub_type == "physical":
                name = f"dc-{self.faker.city().lower().replace(' ', '-')}"
                attributes = {
                    "location": self.faker.address(),
                    "provider": random.choice(
                        ["Equinix", "Digital Realty", "CoreSite", "On-Premise"]
                    ),
                    "power_capacity_kw": random.randint(100, 1000),
                }
            else:
                name = f"{sub_type}-{index:02d}"

        elif entity_type == "security":
            if sub_type == "vulnerability":
                name = f"CVE-{random.randint(2020, 2024)}-{random.randint(1000, 99999)}"
                attributes = {
                    "cve": name,
                    "cvss_score": round(random.uniform(1.0, 10.0), 1),
                    "severity": random.choice(["low", "medium", "high", "critical"]),
                }
            elif sub_type == "compliance":
                framework = random.choice(["SOC2", "ISO27001", "PCI-DSS", "HIPAA"])
                name = f"{framework}-{random.randint(1, 20)}"
                attributes = {
                    "framework": framework,
                    "control_id": f"{framework[:3]}-{random.randint(1, 100)}",
                    "status": random.choice(
                        ["compliant", "non-compliant", "in-progress"]
                    ),
                }
            else:
                name = f"{sub_type}-finding-{index:02d}"

        else:
            name = f"{entity_type}-{sub_type}-{index:02d}"

        return name, attributes

    def seed_projects(self) -> None:
        """Create projects."""
        self.log("\nSeeding Projects...")

        if not self.created["organizations"]:
            self.log("  No organizations - skipping projects")
            return

        projects = [
            ("Platform Modernization", "Migrate legacy systems to cloud-native"),
            ("Security Hardening Q1", "Quarterly security improvements"),
            ("API Gateway v2", "New API gateway implementation"),
            ("Observability Stack", "Implement comprehensive monitoring"),
            ("CI/CD Pipeline", "Modernize deployment pipelines"),
            ("Database Migration", "Migrate from MySQL to PostgreSQL"),
            ("Kubernetes Adoption", "Container orchestration rollout"),
            ("Zero Trust Network", "Implement zero trust architecture"),
        ]

        for name, description in projects[: self.count]:
            result = self._api_post(
                "/api/v1/projects",
                {
                    "name": name,
                    "description": description,
                    "organization_id": self._random_org_id(),
                    "status": random.choice(
                        ["planning", "in_progress", "completed", "on_hold"]
                    ),
                },
            )
            if result:
                self.created["projects"].append(result)
                self.log(f"  Created project: {name}")

    def seed_milestones(self) -> None:
        """Create milestones for projects."""
        self.log("\nSeeding Milestones...")

        if not self.created["projects"]:
            self.log("  No projects to add milestones to")
            return

        milestone_names = [
            "Design Complete",
            "MVP Release",
            "Beta Launch",
            "GA Release",
            "Phase 1 Complete",
            "Security Audit",
        ]

        for project in self.created["projects"][: self.count // 2]:
            for i, name in enumerate(milestone_names[: random.randint(2, 4)]):
                due_date = datetime.now(timezone.utc) + timedelta(days=30 * (i + 1))
                result = self._api_post(
                    "/api/v1/milestones",
                    {
                        "title": name,
                        "description": f"{name} for {project.get('name', 'project')}",
                        "project_id": project.get("id"),
                        "due_date": due_date.isoformat(),
                        "status": random.choice(["open", "closed"]),
                    },
                )
                if result:
                    self.created["milestones"].append(result)
                    self.log(f"  Created milestone: {name}")

    def seed_labels(self) -> None:
        """Create labels for categorization."""
        self.log("\nSeeding Labels...")

        labels = [
            ("bug", "#d73a4a", "Something isn't working"),
            ("enhancement", "#a2eeef", "New feature or request"),
            ("documentation", "#0075ca", "Documentation improvements"),
            ("security", "#ee0701", "Security related"),
            ("performance", "#fbca04", "Performance improvements"),
            ("technical-debt", "#b60205", "Technical debt to address"),
            ("blocked", "#e4e669", "Blocked by external dependency"),
            ("p0-critical", "#d73a4a", "Critical priority"),
            ("p1-high", "#ff7619", "High priority"),
            ("p2-medium", "#fbca04", "Medium priority"),
        ]

        for name, color, description in labels[: self.count]:
            result = self._api_post(
                "/api/v1/labels",
                {
                    "name": name,
                    "color": color,
                    "description": description,
                },
            )
            if result:
                self.created["labels"].append(result)
                self.log(f"  Created label: {name}")

    def seed_issues(self) -> None:
        """Create issues with various statuses."""
        self.log("\nSeeding Issues...")

        # Issues require reporter_id (identity)
        if not self.created["identities"]:
            self.log("  No identities - skipping issues")
            return

        issue_templates = [
            ("Investigate slow API response times", "other"),
            ("Update SSL certificates before expiry", "other"),
            ("Database connection pool exhaustion", "bug"),
            ("Add rate limiting to public endpoints", "feature"),
            ("Document new authentication flow", "other"),
            ("Memory leak in worker processes", "bug"),
            ("Implement caching for dashboard queries", "other"),
            ("Upgrade deprecated dependencies", "other"),
            ("Add health check endpoints", "feature"),
            ("Review and rotate API keys", "other"),
        ]

        for i in range(self.count):
            title, issue_type = random.choice(issue_templates)
            title = f"{title} #{i + 1}"

            # Get a random reporter from created identities
            reporter = random.choice(self.created["identities"])
            reporter_id = reporter.get("id")

            payload: dict[str, Any] = {
                "title": title,
                "description": self.faker.paragraph(nb_sentences=3),
                "status": random.choice(["open", "in_progress", "resolved", "closed"]),
                "priority": random.choice(["low", "medium", "high", "critical"]),
                "issue_type": issue_type,
                "reporter_id": reporter_id,
            }

            # Optionally add organization_id
            if self.created["organizations"]:
                payload["organization_id"] = self._random_org_id()

            result = self._api_post("/api/v1/issues", payload)
            if result:
                self.created["issues"].append(result)
                self.log(f"  Created issue: {title[:50]}...")

    def seed_services(self) -> None:
        """Create microservices."""
        self.log("\nSeeding Services...")

        if not self.created["organizations"]:
            self.log("  No organizations - skipping services")
            return

        services = [
            ("auth-service", "Authentication and authorization"),
            ("api-gateway", "API Gateway and request routing"),
            ("user-service", "User management"),
            ("notification-service", "Email and push notifications"),
            ("payment-service", "Payment processing"),
            ("search-service", "Search and indexing"),
            ("analytics-service", "Analytics and reporting"),
            ("file-service", "File storage and CDN"),
            ("audit-service", "Audit logging"),
            ("scheduler-service", "Job scheduling"),
        ]

        for name, description in services[: self.count]:
            result = self._api_post(
                "/api/v1/services",
                {
                    "name": name,
                    "description": description,
                    "organization_id": self._random_org_id(),
                    "deployment_method": random.choice(DEPLOYMENT_METHODS),
                    "language": random.choice(LANGUAGES),
                    "repository_url": f"https://github.com/example/{name}",
                    "sla_uptime": random.choice([99.9, 99.95, 99.99]),
                    "status": random.choice(["active", "active", "maintenance"]),
                },
            )
            if result:
                self.created["services"].append(result)
                self.log(f"  Created service: {name}")

    def seed_software(self) -> None:
        """Create software licenses."""
        self.log("\nSeeding Software...")

        if not self.created["organizations"]:
            self.log("  No organizations - skipping software")
            return

        for vendor, category in SOFTWARE_VENDORS[: self.count]:
            result = self._api_post(
                "/api/v1/software",
                {
                    "name": f"{vendor} Enterprise",
                    "description": f"{category} software from {vendor}",
                    "organization_id": self._random_org_id(),
                    "vendor": vendor,
                    "version": self._random_version(),
                    "software_type": random.choice(
                        ["commercial", "open_source", "internal"]
                    ),
                    "seats": random.randint(10, 500),
                    "cost_monthly": float(random.randint(100, 5000)),
                    "renewal_date": (
                        datetime.now(timezone.utc)
                        + timedelta(days=random.randint(30, 365))
                    )
                    .date()
                    .isoformat(),
                    "is_active": True,
                },
            )
            if result:
                self.created["software"].append(result)
                self.log(f"  Created software: {vendor}")

    def seed_ipam(self) -> None:
        """Create IPAM prefixes, VLANs, and addresses."""
        self.log("\nSeeding IPAM...")

        if not self.created["organizations"]:
            self.log("  No organizations - skipping IPAM")
            return

        # Create prefixes
        self.log("  Creating prefixes...")
        prefixes = [
            ("10.0.0.0/8", "Private - Class A"),
            ("172.16.0.0/12", "Private - Class B"),
            ("192.168.0.0/16", "Private - Class C"),
        ]

        for cidr, description in prefixes:
            result = self._api_post(
                "/api/v1/ipam/prefixes",
                {
                    "prefix": cidr,
                    "description": description,
                    "organization_id": self._random_org_id(),
                    "status": "active",
                    "is_pool": True,
                },
            )
            if result:
                self.created["ipam_prefixes"].append(result)
                self.log(f"    Created prefix: {cidr}")

        # Create VLANs
        self.log("  Creating VLANs...")
        vlans = [
            (10, "Management"),
            (20, "Servers"),
            (30, "Workstations"),
            (40, "Guest"),
            (100, "DMZ"),
        ]

        for vid, name in vlans[: self.count // 2]:
            result = self._api_post(
                "/api/v1/ipam/vlans",
                {
                    "vid": vid,
                    "name": name,
                    "description": f"VLAN {vid} - {name}",
                    "organization_id": self._random_org_id(),
                    "status": "active",
                },
            )
            if result:
                self.created["ipam_vlans"].append(result)
                self.log(f"    Created VLAN: {vid} ({name})")

        # Create individual addresses (need prefix_id)
        if self.created["ipam_prefixes"]:
            self.log("  Creating addresses...")
            for i in range(min(self.count, 10)):
                ip = f"10.0.{random.randint(1, 254)}.{random.randint(1, 254)}/32"
                prefix = random.choice(self.created["ipam_prefixes"])
                result = self._api_post(
                    "/api/v1/ipam/addresses",
                    {
                        "address": ip,
                        "description": f"Address {i + 1}",
                        "prefix_id": prefix.get("id"),
                        "status": random.choice(["active", "reserved", "dhcp"]),
                    },
                )
                if result:
                    self.created["ipam_addresses"].append(result)
                    self.log(f"    Created address: {ip}")

    def seed_dependencies(self) -> None:
        """Create dependencies between entities."""
        self.log("\nSeeding Dependencies...")

        if len(self.created["entities"]) < 2:
            self.log("  Not enough entities to create dependencies")
            return

        dependency_types = ["depends_on", "related_to", "part_of"]

        for i in range(min(self.count, len(self.created["entities"]) - 1)):
            source = random.choice(self.created["entities"])
            target = random.choice(
                [e for e in self.created["entities"] if e.get("id") != source.get("id")]
            )

            result = self._api_post(
                "/api/v1/dependencies",
                {
                    "source_type": "entity",
                    "source_id": source.get("id"),
                    "target_type": "entity",
                    "target_id": target.get("id"),
                    "dependency_type": random.choice(dependency_types),
                },
            )
            if result:
                self.created["dependencies"].append(result)
                self.log(
                    f"  Created dependency: {source.get('name')} -> {target.get('name')}"
                )

    def seed_data_stores(self) -> None:
        """Create data stores."""
        self.log("\nSeeding Data Stores...")

        if not self.created["organizations"]:
            self.log("  No organizations - skipping data stores")
            return

        storage_types = [
            "database",
            "file_storage",
            "data_warehouse",
            "cache",
            "blob_storage",
            "message_queue",
        ]
        providers = ["AWS", "GCP", "Azure", "On-Premise", "MinIO"]
        regions = ["us-west-2", "us-east-1", "eu-west-1", "ap-southeast-1"]
        classifications = ["public", "internal", "confidential", "restricted"]

        for i in range(self.count):
            storage_type = random.choice(storage_types)
            provider = random.choice(providers)

            name = f"{storage_type}-{provider.lower()}-{i + 1:02d}"

            result = self._api_post(
                "/api/v1/data-stores",
                {
                    "name": name,
                    "description": f"{storage_type.replace('_', ' ').title()} on {provider}",
                    "organization_id": self._random_org_id(),
                    "storage_type": storage_type,
                    "storage_provider": provider,
                    "location_region": random.choice(regions),
                    "data_classification": random.choice(classifications),
                    "encryption_at_rest": random.random() > 0.3,
                    "encryption_in_transit": random.random() > 0.2,
                    "retention_days": random.choice([30, 90, 180, 365, 730]),
                    "backup_enabled": random.random() > 0.4,
                    "backup_frequency": random.choice(["daily", "hourly", "weekly"]),
                    "access_control_type": random.choice(["iam", "rbac", "private"]),
                    "compliance_frameworks": random.sample(
                        ["SOC2", "HIPAA", "GDPR", "PCI-DSS"], k=random.randint(0, 2)
                    ),
                    "contains_pii": random.random() > 0.7,
                    "contains_phi": random.random() > 0.9,
                    "contains_pci": random.random() > 0.8,
                    "size_bytes": random.randint(1000000, 10000000000),
                    "is_active": True,
                },
            )
            if result:
                self.created["data_stores"].append(result)
                self.log(f"  Created data store: {name}")

    def seed_secret_providers(self) -> None:
        """Create secret providers."""
        self.log("\nSeeding Secret Providers...")

        if not self.created["organizations"]:
            self.log("  No organizations - skipping secret providers")
            return

        providers = [
            ("AWS Secrets Manager", "aws", "AWS-managed secrets"),
            ("HashiCorp Vault", "vault", "Vault secrets storage"),
            ("GCP Secret Manager", "gcp", "Google Cloud secrets"),
            ("Azure Key Vault", "azure", "Azure secrets management"),
            ("Builtin", "builtin", "Built-in secret storage"),
        ]

        for name, provider_type, description in providers[: max(2, self.count // 3)]:
            result = self._api_post(
                "/api/v1/secret-providers",
                {
                    "name": name,
                    "description": description,
                    "provider_type": provider_type,
                    "organization_id": self._random_org_id(),
                    "config_json": {"endpoint": f"https://{provider_type}.example.com"},
                    "is_active": True,
                },
            )
            if result:
                self.created["secret_providers"].append(result)
                self.log(f"  Created secret provider: {name}")

    def seed_secrets(self) -> None:
        """Create secrets."""
        self.log("\nSeeding Secrets...")

        # Need at least one secret provider
        if not self.created["secret_providers"]:
            self.log("  No secret providers - seeding providers first")
            self.seed_secret_providers()

        if not self.created["secret_providers"]:
            self.log("  Failed to create secret providers - skipping secrets")
            return

        secret_types = ["generic", "api_key", "password", "certificate", "ssh_key"]
        secret_names = [
            "database-password",
            "api-key-stripe",
            "api-key-sendgrid",
            "ssh-deploy-key",
            "tls-certificate",
            "jwt-signing-key",
            "encryption-key",
            "oauth-client-secret",
        ]

        for i in range(self.count):
            provider = random.choice(self.created["secret_providers"])
            secret_type = random.choice(secret_types)
            name = random.choice(secret_names) + f"-{i + 1}"

            result = self._api_post(
                "/api/v1/secrets",
                {
                    "name": name,
                    "provider_id": provider.get("id"),
                    "provider_path": f"/secrets/{name}",
                    "secret_type": secret_type,
                    "is_kv": secret_type == "generic",
                    "organization_id": self._random_org_id(),
                    "metadata": {
                        "created_by": "mock_seeder",
                        "purpose": f"{secret_type} for testing",
                    },
                },
            )
            if result:
                self.created["secrets"].append(result)
                self.log(f"  Created secret: {name}")

    def seed_api_keys(self) -> None:
        """Create API keys for identities."""
        self.log("\nSeeding API Keys...")

        if not self.created["identities"]:
            self.log("  No identities - skipping API keys")
            return

        # Create 1-3 API keys for a few random identities
        identities_to_use = random.sample(
            self.created["identities"],
            k=min(max(2, self.count // 3), len(self.created["identities"])),
        )

        for identity in identities_to_use:
            for i in range(random.randint(1, 3)):
                name = f"{identity.get('username', 'user')}-key-{i + 1}"

                # Calculate expiration (30-365 days from now)
                expires_days = random.randint(30, 365)
                expires_at = (
                    datetime.now(timezone.utc) + timedelta(days=expires_days)
                ).isoformat()

                result = self._api_post(
                    "/api/v1/api-keys",
                    {
                        "name": name,
                        "expires_at": expires_at,
                    },
                )
                if result:
                    self.created["api_keys"].append(result)
                    self.log(
                        f"  Created API key: {name} for {identity.get('username')}"
                    )

    def seed_certificates(self) -> None:
        """Create certificates."""
        self.log("\nSeeding Certificates...")

        if not self.created["organizations"]:
            self.log("  No organizations - skipping certificates")
            return

        creators = ["letsencrypt", "digicert", "self_signed", "certbot"]
        cert_types = ["server_cert", "wildcard", "san", "client_cert"]
        domains = [
            "api.example.com",
            "*.example.com",
            "app.example.com",
            "www.example.com",
            "dashboard.example.com",
        ]

        for i in range(self.count):
            creator = random.choice(creators)
            cert_type = random.choice(cert_types)
            common_name = random.choice(domains)

            # Generate issue and expiration dates
            issue_date = datetime.now(timezone.utc) - timedelta(
                days=random.randint(1, 300)
            )
            expiration_date = issue_date + timedelta(days=random.choice([90, 180, 365]))

            result = self._api_post(
                "/api/v1/certificates",
                {
                    "name": f"cert-{common_name.replace('*', 'wildcard').replace('.', '-')}-{i + 1}",
                    "description": f"SSL/TLS certificate for {common_name}",
                    "organization_id": self._random_org_id(),
                    "creator": creator,
                    "cert_type": cert_type,
                    "common_name": common_name,
                    "subject_alternative_names": (
                        [common_name, f"www.{common_name}"]
                        if cert_type == "san"
                        else []
                    ),
                    "key_algorithm": random.choice(["RSA", "ECDSA"]),
                    "key_size": random.choice([2048, 4096, 256]),
                    "signature_algorithm": random.choice(
                        ["SHA256WithRSA", "SHA384WithRSA", "SHA256WithECDSA"]
                    ),
                    "issue_date": issue_date.date().isoformat(),
                    "expiration_date": expiration_date.date().isoformat(),
                    "auto_renew": random.random() > 0.5,
                    "renewal_days_before": 30,
                    "is_active": True,
                },
            )
            if result:
                self.created["certificates"].append(result)
                self.log(f"  Created certificate: {common_name}")

    def _random_org_id(self) -> int | None:
        """Get random organization ID from created orgs."""
        if self.created["organizations"]:
            return random.choice(self.created["organizations"]).get("id")
        return None

    def _random_tags(self) -> list[str]:
        """Generate random tags."""
        all_tags = [
            "production",
            "staging",
            "development",
            "critical",
            "monitored",
            "backup-enabled",
            "auto-scaling",
            "high-availability",
            "deprecated",
            "legacy",
        ]
        return random.sample(all_tags, k=random.randint(1, 4))

    def _random_version(self) -> str:
        """Generate random semantic version."""
        return f"{random.randint(1, 5)}.{random.randint(0, 20)}.{random.randint(0, 10)}"

    def seed_all(self) -> None:
        """Run all seeders in dependency order."""
        print(f"\n{'=' * 60}")
        print("Elder Mock Data Seeder")
        print(f"{'=' * 60}")
        print(f"Target: {self.base_url}")
        print(f"Count per type: {self.count}")
        print(f"Dry run: {self.dry_run}")
        print(f"{'=' * 60}")

        if not self.authenticate():
            print("\nFailed to authenticate. Aborting.", file=sys.stderr)
            sys.exit(1)

        # Run seeders in dependency order
        self.seed_organizations()
        self.seed_identities()
        self.seed_identity_groups()
        self.seed_entities()
        self.seed_projects()
        self.seed_milestones()
        self.seed_labels()
        self.seed_issues()
        self.seed_services()
        self.seed_software()
        self.seed_ipam()
        self.seed_dependencies()
        self.seed_data_stores()
        self.seed_secret_providers()
        self.seed_secrets()
        self.seed_api_keys()
        self.seed_certificates()

        # Print summary
        self._print_summary()

    def _print_summary(self) -> None:
        """Print summary of created resources."""
        print(f"\n{'=' * 60}")
        print("Summary")
        print(f"{'=' * 60}")

        total = 0
        for resource_type, items in self.created.items():
            count = len(items)
            total += count
            if count > 0:
                print(f"  {resource_type.replace('_', ' ').title()}: {count}")

        print(f"{'=' * 60}")
        print(f"  Total created: {total}")
        print(f"{'=' * 60}\n")


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Seed Elder with mock data for local development",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                              # Use defaults
  %(prog)s --count 5 -v                 # Create 5 items per type, verbose
  %(prog)s --base-url http://api:5000   # Different API URL
  %(prog)s --dry-run                    # Preview without creating
        """,
    )

    parser.add_argument(
        "--base-url",
        default="http://localhost:4000",
        help="API base URL (default: http://localhost:4000)",
    )
    parser.add_argument(
        "--tenant-id",
        type=int,
        default=1,
        help="Tenant ID for authentication (default: 1)",
    )
    parser.add_argument(
        "--email",
        default="admin@localhost",
        help="Admin email (default: admin@localhost)",
    )
    parser.add_argument(
        "--password",
        default="admin123",
        help="Admin password (default: admin123)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=10,
        help="Number of items per resource type (default: 10)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show detailed progress",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be created without making requests",
    )
    parser.add_argument(
        "--host-header",
        default="",
        help="Override Host header (for beta bypass URL, e.g. elder.penguintech.cloud)",
    )
    parser.add_argument(
        "--no-verify-ssl",
        action="store_true",
        help="Skip SSL certificate verification",
    )

    args = parser.parse_args()

    seeder = MockDataSeeder(
        base_url=args.base_url,
        tenant_id=args.tenant_id,
        email=args.email,
        password=args.password,
        count=args.count,
        verbose=args.verbose,
        host_header=args.host_header,
        verify_ssl=not args.no_verify_ssl,
        dry_run=args.dry_run,
    )

    try:
        seeder.seed_all()
    except KeyboardInterrupt:
        print("\n\nAborted by user.", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
