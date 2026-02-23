# Elder Deployment Guide

Guide for deploying Elder in production environments.

## Deployment Options

Elder can be deployed in multiple ways depending on your infrastructure:

1. **[Docker Compose](#docker-compose-deployment)** - Quick deployment for small-medium scale
2. **[Kubernetes](#kubernetes-deployment)** - Production-grade orchestration
3. **[Manual Deployment](#manual-deployment)** - Traditional deployment on VMs

## Prerequisites

### Minimum Requirements

- **CPU**: 2 cores
- **RAM**: 4GB
- **Storage**: 20GB (database grows with data)
- **OS**: Linux (Ubuntu 22.04+ recommended)

### Production Requirements

- **CPU**: 4+ cores
- **RAM**: 8GB+
- **Storage**: 100GB+ SSD
- **OS**: Linux with Docker support
- **Database**: PostgreSQL 15+
- **Cache**: Redis 7+

### Required Software

- Docker 24.0+
- Docker Compose 2.20+
- (Kubernetes): kubectl, Helm 3+

## Docker Compose Deployment

### Quick Start

```bash
# 1. Clone repository
git clone https://github.com/penguintechinc/elder.git
cd elder

# 2. Configure environment
cp .env.example .env
nano .env  # Edit configuration

# 3. Start services
docker-compose up -d

# 4. Verify deployment
curl http://localhost:5000/healthz
```

### Configuration

Edit `.env` file with your settings:

```bash
# Database
POSTGRES_DB=elder
POSTGRES_USER=elder
POSTGRES_PASSWORD=<secure-password>

# Redis
REDIS_PASSWORD=<secure-password>

# API
SECRET_KEY=<random-secret-key>
FLASK_ENV=production

# Admin User
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<secure-password>
ADMIN_EMAIL=admin@yourdomain.com

# License (optional)
LICENSE_KEY=PENG-XXXX-XXXX-XXXX-XXXX-XXXX
```

### Services

Docker Compose starts the following services:

- `postgres` - PostgreSQL database
- `redis` - Redis cache
- `api` - Flask REST API
- `web` - React web UI
- `worker` - Data sync service (optional)
- `prometheus` - Metrics collection
- `grafana` - Monitoring dashboards

### Service Management

```bash
# Start all services
docker-compose up -d

# Start specific services
docker-compose up -d api web postgres redis

# Stop services
docker-compose stop

# Stop and remove containers
docker-compose down

# View logs
docker-compose logs -f api

# Restart service
docker-compose restart api

# Check status
docker-compose ps
```

### Database Migrations

```bash
# Run migrations
docker-compose exec api alembic upgrade head

# Check migration status
docker-compose exec api alembic current

# Rollback migration
docker-compose exec api alembic downgrade -1
```

### Backup & Restore

#### Database Backup

```bash
# Create backup
docker-compose exec postgres pg_dump -U elder elder > backup.sql

# Or with timestamp
docker-compose exec postgres pg_dump -U elder elder > backup-$(date +%Y%m%d).sql
```

#### Database Restore

```bash
# Restore from backup
docker-compose exec -T postgres psql -U elder elder < backup.sql
```

#### Full Backup

```bash
# Backup volumes
docker run --rm \
  -v elder_postgres_data:/data \
  -v $(pwd):/backup \
  alpine tar czf /backup/postgres-data.tar.gz -C /data .
```

## Kubernetes Deployment

### Using Helm

```bash
# Add Elder Helm repository
helm repo add elder https://charts.penguintech.io/elder
helm repo update

# Install Elder
helm install elder elder/elder \
  --namespace elder \
  --create-namespace \
  --set postgresql.auth.password=<password> \
  --set redis.auth.password=<password>

# Upgrade
helm upgrade elder elder/elder \
  --namespace elder

# Uninstall
helm uninstall elder --namespace elder
```

### Using Kubectl

```bash
# Apply Kubernetes manifests
kubectl apply -f infrastructure/k8s/

# Check deployment
kubectl get pods -n elder
kubectl get svc -n elder

# View logs
kubectl logs -f deployment/elder-api -n elder
```

### Kubernetes Resources

**Deployments:**
- `elder-api` - API server (2+ replicas)
- `elder-web` - Web UI
- `elder-worker` - Worker service (1 replica)

**StatefulSets:**
- `postgresql` - Database
- `redis` - Cache

**Services:**
- `elder-api` - ClusterIP
- `elder-web` - LoadBalancer/Ingress
- `postgresql` - ClusterIP
- `redis` - ClusterIP

**ConfigMaps:**
- `elder-config` - Application configuration

**Secrets:**
- `elder-secrets` - Sensitive data (passwords, keys)

### Ingress Configuration

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: elder-ingress
  namespace: elder
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
spec:
  tls:
    - hosts:
        - elder.yourdomain.com
      secretName: elder-tls
  rules:
    - host: elder.yourdomain.com
      http:
        paths:
          - path: /api
            pathType: Prefix
            backend:
              service:
                name: elder-api
                port:
                  number: 5000
          - path: /
            pathType: Prefix
            backend:
              service:
                name: elder-web
                port:
                  number: 80
```

### Scaling

```bash
# Scale API replicas
kubectl scale deployment elder-api --replicas=5 -n elder

# Horizontal Pod Autoscaler
kubectl autoscale deployment elder-api \
  --cpu-percent=70 \
  --min=2 \
  --max=10 \
  -n elder
```

## Manual Deployment

### System Preparation

```bash
# Install dependencies (Ubuntu/Debian)
sudo apt-get update
sudo apt-get install -y \
  python3.13 \
  python3-pip \
  postgresql-15 \
  redis-server \
  nginx

# Install Python packages
pip3 install -r requirements.txt
```

### PostgreSQL Setup

```bash
# Create database and user
sudo -u postgres psql <<EOF
CREATE DATABASE elder;
CREATE USER elder WITH ENCRYPTED PASSWORD '<password>';
GRANT ALL PRIVILEGES ON DATABASE elder TO elder;
EOF

# Configure pg_hba.conf for password authentication
sudo nano /etc/postgresql/15/main/pg_hba.conf
# Add: host elder elder 127.0.0.1/32 md5

# Restart PostgreSQL
sudo systemctl restart postgresql
```

### Redis Setup

```bash
# Configure Redis
sudo nano /etc/redis/redis.conf
# Set: requirepass <password>

# Restart Redis
sudo systemctl restart redis-server
```

### Application Setup

```bash
# Clone repository
git clone https://github.com/penguintechinc/elder.git
cd elder

# Configure environment
cp .env.example .env
nano .env

# Run migrations
alembic upgrade head

# Start API (systemd service)
sudo systemctl start elder-api

# Start worker (systemd service)
sudo systemctl start elder-worker
```

### Nginx Configuration

```nginx
server {
    listen 80;
    server_name elder.yourdomain.com;

    # API
    location /api {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # Web UI
    location / {
        root /var/www/elder;
        try_files $uri /index.html;
    }
}
```

## Production Checklist

### Security

- [ ] Change all default passwords
- [ ] Generate secure SECRET_KEY
- [ ] Enable TLS/HTTPS
- [ ] Configure firewall rules
- [ ] Set up fail2ban
- [ ] Enable audit logging
- [ ] Review CORS settings
- [ ] Configure rate limiting

### Performance

- [ ] Set appropriate PostgreSQL shared_buffers
- [ ] Configure PostgreSQL connection pooling
- [ ] Enable Redis persistence
- [ ] Set up database connection pooling
- [ ] Configure caching headers
- [ ] Enable gzip compression

### Monitoring

- [ ] Configure Prometheus scraping
- [ ] Set up Grafana dashboards
- [ ] Configure alerting rules
- [ ] Set up log aggregation
- [ ] Configure uptime monitoring
- [ ] Set up error tracking

### Backup

- [ ] Configure automated database backups
- [ ] Test backup restoration
- [ ] Set up off-site backup storage
- [ ] Configure backup retention policy
- [ ] Document backup procedures

### High Availability

- [ ] Deploy multiple API replicas
- [ ] Configure database replication
- [ ] Set up Redis Sentinel/Cluster
- [ ] Configure load balancer
- [ ] Set up health checks
- [ ] Plan failover procedures

## Monitoring & Observability

### Health Checks

```bash
# API health
curl http://localhost:5000/healthz

# Database connectivity
docker-compose exec postgres pg_isready

# Redis connectivity
docker-compose exec redis redis-cli ping
```

### Prometheus Metrics

```bash
# API metrics
curl http://localhost:5000/metrics

# View in Prometheus
open http://localhost:9090
```

### Grafana Dashboards

```bash
# Access Grafana
open http://localhost:3001

# Default credentials
Username: admin
Password: admin (change on first login)
```

### Log Aggregation

```bash
# View logs
docker-compose logs -f api

# Export logs
docker-compose logs api > api-logs.txt

# Follow specific service
docker-compose logs -f --tail=100 worker
```

## Troubleshooting

### Common Issues

#### Database Connection Issues

```bash
# Check PostgreSQL is running
docker-compose ps postgres

# Check connection
docker-compose exec postgres psql -U elder -d elder

# View PostgreSQL logs
docker-compose logs postgres
```

#### API Not Starting

```bash
# Check logs
docker-compose logs api

# Verify environment variables
docker-compose exec api env | grep -E "POSTGRES|REDIS|SECRET_KEY"

# Test database connection
docker-compose exec api python3 -c "from apps.api.main import create_app; app = create_app(); print('OK')"
```

#### Worker Not Syncing

```bash
# Check worker logs
docker-compose logs worker

# Test connectivity
docker-compose exec worker python3 /app/apps/worker/test_connectivity.py

# Verify configuration
docker-compose exec worker env | grep -E "AWS|GCP|LDAP"
```

## Performance Tuning

### PostgreSQL Tuning

```sql
-- In postgresql.conf
shared_buffers = 256MB
effective_cache_size = 1GB
maintenance_work_mem = 64MB
max_connections = 100
```

### Redis Tuning

```conf
# In redis.conf
maxmemory 512mb
maxmemory-policy allkeys-lru
```

### API Tuning

```bash
# In .env
DB_POOL_SIZE=20
DB_MAX_OVERFLOW=10
CACHE_TTL=300
```

## Upgrading

### Docker Compose Upgrade

```bash
# Pull latest images
docker-compose pull

# Stop services
docker-compose down

# Start with new images
docker-compose up -d

# Run migrations
docker-compose exec api alembic upgrade head
```

### Kubernetes Upgrade

```bash
# Upgrade with Helm
helm upgrade elder elder/elder \
  --namespace elder \
  --reuse-values

# Rollback if needed
helm rollback elder -n elder
```

## Security Hardening

### TLS Configuration

```bash
# Generate certificates (Let's Encrypt)
certbot certonly --standalone -d elder.yourdomain.com

# Configure nginx
server {
    listen 443 ssl http2;
    ssl_certificate /etc/letsencrypt/live/elder.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/elder.yourdomain.com/privkey.pem;

    # Modern TLS configuration
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
}
```

### Firewall Rules

```bash
# Allow only necessary ports
sudo ufw allow 80/tcp   # HTTP
sudo ufw allow 443/tcp  # HTTPS
sudo ufw allow 22/tcp   # SSH
sudo ufw enable
```

### Database Security

```sql
-- Revoke public schema privileges
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO elder;

-- Use SSL for connections
ALTER SYSTEM SET ssl = on;
```

## Further Reading

- [Architecture Documentation](../architecture/README.md)
- [API Documentation](../api/README.md)
- [Database Schema](../DATABASE.md)
- [Worker Configuration](../worker/README.md)
