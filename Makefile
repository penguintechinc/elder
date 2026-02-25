# Elder - Entity Relationship Tracking Application Makefile
# Provides common development tasks for Elder

.PHONY: help setup dev test build clean lint format docker deploy

# Default target
.DEFAULT_GOAL := help

# Variables
PROJECT_NAME := elder
VERSION := $(shell cat .version 2>/dev/null || echo "0.1.0")
DOCKER_REGISTRY := ghcr.io
DOCKER_ORG := penguintechinc
PYTHON_VERSION := 3.13

# Colors for output
RED := \033[31m
GREEN := \033[32m
YELLOW := \033[33m
BLUE := \033[34m
RESET := \033[0m

# Help target
help: ## Show this help message
	@echo "$(BLUE)Elder - Entity Relationship Tracking Application$(RESET)"
	@echo ""
	@echo "$(GREEN)Setup Commands:$(RESET)"
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / && /Setup/ {printf "  $(YELLOW)%-25s$(RESET) %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""
	@echo "$(GREEN)Development Commands:$(RESET)"
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / && /Development/ {printf "  $(YELLOW)%-25s$(RESET) %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""
	@echo "$(GREEN)Testing Commands:$(RESET)"
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / && /Testing/ {printf "  $(YELLOW)%-25s$(RESET) %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""
	@echo "$(GREEN)Database Commands:$(RESET)"
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / && /Database/ {printf "  $(YELLOW)%-25s$(RESET) %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""
	@echo "$(GREEN)Docker Commands:$(RESET)"
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / && /Docker/ {printf "  $(YELLOW)%-25s$(RESET) %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""
	@echo "$(GREEN)License Commands:$(RESET)"
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / && /License/ {printf "  $(YELLOW)%-25s$(RESET) %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# Setup Commands
setup: ## Setup - Install dependencies and initialize the project
	@echo "$(BLUE)Setting up Elder...$(RESET)"
	@$(MAKE) setup-env
	@$(MAKE) setup-python
	@echo "$(GREEN)Setup complete!$(RESET)"
	@echo "$(YELLOW)Next steps:$(RESET)"
	@echo "  1. Edit .env with your configuration"
	@echo "  2. Run 'make dev' to start development environment"
	@echo "  3. Run 'make db-migrate' to initialize database"

setup-env: ## Setup - Create environment file from template
	@if [ ! -f .env ]; then \
		echo "$(YELLOW)Creating .env file...$(RESET)"; \
		echo "# Elder Environment Configuration" > .env; \
		echo "FLASK_ENV=development" >> .env; \
		echo "SECRET_KEY=dev-secret-key-change-in-production" >> .env; \
		echo "" >> .env; \
		echo "# Database" >> .env; \
		echo "POSTGRES_DB=elder" >> .env; \
		echo "POSTGRES_USER=elder" >> .env; \
		echo "POSTGRES_PASSWORD=elder_dev_password" >> .env; \
		echo "DATABASE_URL=postgresql://elder:elder_dev_password@localhost:5432/elder" >> .env; \
		echo "" >> .env; \
		echo "# Redis" >> .env; \
		echo "REDIS_PASSWORD=elder_redis_password" >> .env; \
		echo "REDIS_URL=redis://:elder_redis_password@localhost:6379/0" >> .env; \
		echo "" >> .env; \
		echo "# Admin User (optional)" >> .env; \
		echo "ADMIN_USERNAME=admin" >> .env; \
		echo "ADMIN_PASSWORD=" >> .env; \
		echo "ADMIN_EMAIL=admin@localhost.local" >> .env; \
		echo "" >> .env; \
		echo "# License (optional)" >> .env; \
		echo "LICENSE_KEY=" >> .env; \
		echo "$(GREEN).env file created!$(RESET)"; \
	else \
		echo "$(YELLOW).env file already exists$(RESET)"; \
	fi

setup-python: ## Setup - Install Python dependencies
	@echo "$(BLUE)Setting up Python dependencies...$(RESET)"
	@python3 --version || (echo "$(RED)Python $(PYTHON_VERSION) not installed$(RESET)" && exit 1)
	@pip install --upgrade pip
	@pip install -r requirements.txt
	@echo "$(GREEN)Python dependencies installed!$(RESET)"

# Development Commands
dev: ## Development - Start development environment
	@echo "$(BLUE)Starting Elder development environment...$(RESET)"
	@docker-compose up -d postgres redis
	@echo "$(YELLOW)Waiting for services to be ready...$(RESET)"
	@sleep 5
	@echo "$(GREEN)Services are ready!$(RESET)"
	@echo "$(YELLOW)Run 'make dev-api' to start the Flask API$(RESET)"

dev-api: ## Development - Start Flask API locally
	@echo "$(BLUE)Starting Elder API...$(RESET)"
	@export FLASK_APP=apps.api.main:create_app && \
	export FLASK_ENV=development && \
	flask run --host=0.0.0.0 --port=5000

dev-all: ## Development - Start all services with docker-compose
	@echo "$(BLUE)Starting all Elder services...$(RESET)"
	@docker-compose up -d
	@echo "$(GREEN)All services started!$(RESET)"
	@echo "$(YELLOW)API: http://localhost:5000$(RESET)"
	@echo "$(YELLOW)Prometheus: http://localhost:9090$(RESET)"
	@echo "$(YELLOW)Grafana: http://localhost:3001$(RESET)"

dev-logs: ## Development - Show docker-compose logs
	@docker-compose logs -f

dev-stop: ## Development - Stop development environment
	@echo "$(BLUE)Stopping Elder development environment...$(RESET)"
	@docker-compose down
	@echo "$(GREEN)Development environment stopped$(RESET)"

dev-restart: ## Development - Restart development environment
	@$(MAKE) dev-stop
	@$(MAKE) dev-all

generate-grpc: ## Development - Generate Python gRPC code from protobuf schemas
	@echo "$(BLUE)Generating Python gRPC code...$(RESET)"
	@docker run --rm -v $(PWD):/app -w /app python:3.13-slim bash -c "\
		pip install --quiet grpcio-tools protobuf && \
		python3 -m grpc_tools.protoc \
			-Iapps/api/grpc/proto \
			--python_out=apps/api/grpc/generated \
			--grpc_python_out=apps/api/grpc/generated \
			apps/api/grpc/proto/*.proto && \
		cd apps/api/grpc/generated && \
		for file in *.py; do \
			sed -i 's/^import \\([a-z_]*\\)_pb2/from . import \\1_pb2/g' \$\$file 2>/dev/null || true; \
		done"
	@touch apps/api/grpc/generated/__init__.py
	@echo "$(GREEN)✓ gRPC code generated successfully!$(RESET)"

# Testing Commands
test: ## Testing - Run all tests
	@echo "$(BLUE)Running Elder tests...$(RESET)"
	@pytest tests/ -v --cov=apps --cov-report=term-missing

test-unit: ## Testing - Run unit tests only
	@echo "$(BLUE)Running unit tests...$(RESET)"
	@pytest tests/unit/ -v

test-integration: ## Testing - Run integration tests only
	@echo "$(BLUE)Running integration tests...$(RESET)"
	@pytest tests/integration/ -v

test-coverage: ## Testing - Generate coverage report
	@echo "$(BLUE)Generating coverage report...$(RESET)"
	@pytest tests/ --cov=apps --cov-report=html
	@echo "$(GREEN)Coverage report generated in htmlcov/$(RESET)"

test-ui: ## Testing - Run Playwright web UI tests (headless)
	@echo "$(BLUE)Running Playwright web UI tests...$(RESET)"
	@cd web && npm run test:e2e
	@echo "$(GREEN)Web UI tests complete!$(RESET)"

test-ui-headed: ## Testing - Run Playwright web UI tests with UI
	@echo "$(BLUE)Running Playwright web UI tests (headed mode)...$(RESET)"
	@cd web && npm run test:e2e:ui
	@echo "$(GREEN)Web UI tests complete! View results in playwright report.$(RESET)"

test-ui-debug: ## Testing - Run Playwright with debug mode
	@echo "$(BLUE)Running Playwright in debug mode...$(RESET)"
	@cd web && npm run test:e2e:debug
	@echo "$(GREEN)Debug session ended$(RESET)"

# Code Quality Commands
lint: ## Testing - Run linting checks
	@echo "$(BLUE)Running linters...$(RESET)"
	@flake8 apps/ shared/
	@mypy apps/ shared/
	@echo "$(GREEN)Linting complete!$(RESET)"

format: ## Testing - Format code with black and isort
	@echo "$(BLUE)Formatting code...$(RESET)"
	@black apps/ shared/ tests/
	@isort apps/ shared/ tests/
	@echo "$(GREEN)Code formatted!$(RESET)"

format-check: ## Testing - Check code formatting
	@echo "$(BLUE)Checking code formatting...$(RESET)"
	@black --check apps/ shared/ tests/
	@isort --check apps/ shared/ tests/

# Database Commands
db-migrate: ## Database - Run database migrations
	@echo "$(BLUE)Running database migrations...$(RESET)"
	@alembic upgrade head
	@echo "$(GREEN)Migrations complete!$(RESET)"

db-create-migration: ## Database - Create a new migration
	@read -p "Enter migration message: " msg; \
	alembic revision --autogenerate -m "$$msg"

db-downgrade: ## Database - Rollback one migration
	@echo "$(YELLOW)Rolling back one migration...$(RESET)"
	@alembic downgrade -1

db-reset: ## Database - Reset database (WARNING: destroys all data)
	@echo "$(RED)WARNING: This will destroy all data!$(RESET)"
	@read -p "Are you sure? [y/N]: " confirm; \
	if [ "$$confirm" = "y" ]; then \
		docker-compose down -v; \
		docker-compose up -d postgres redis; \
		sleep 5; \
		alembic upgrade head; \
		echo "$(GREEN)Database reset complete!$(RESET)"; \
	else \
		echo "$(YELLOW)Cancelled$(RESET)"; \
	fi

db-shell: ## Database - Open PostgreSQL shell
	@docker-compose exec postgres psql -U elder -d elder

db-backup: ## Database - Create database backup
	@echo "$(BLUE)Creating database backup...$(RESET)"
	@mkdir -p backups
	@docker-compose exec -T postgres pg_dump -U elder elder > backups/elder_$$(date +%Y%m%d_%H%M%S).sql
	@echo "$(GREEN)Backup created in backups/$(RESET)"

# Docker Commands
docker-build: ## Docker - Build Docker image
	@echo "$(BLUE)Building Elder Docker image...$(RESET)"
	@docker build -t $(PROJECT_NAME):$(VERSION) -f apps/api/Dockerfile .
	@echo "$(GREEN)Docker image built: $(PROJECT_NAME):$(VERSION)$(RESET)"

docker-build-multiarch: ## Docker - Build multi-architecture Docker images
	@echo "$(BLUE)Building multi-arch Docker images...$(RESET)"
	@docker buildx build --platform linux/amd64,linux/arm64 \
		-t $(DOCKER_REGISTRY)/$(DOCKER_ORG)/$(PROJECT_NAME):$(VERSION) \
		-t $(DOCKER_REGISTRY)/$(DOCKER_ORG)/$(PROJECT_NAME):latest \
		-f apps/api/Dockerfile .
	@echo "$(GREEN)Multi-arch Docker images built!$(RESET)"

docker-push: ## Docker - Push Docker image to registry
	@echo "$(BLUE)Pushing Docker image...$(RESET)"
	@docker push $(DOCKER_REGISTRY)/$(DOCKER_ORG)/$(PROJECT_NAME):$(VERSION)
	@docker push $(DOCKER_REGISTRY)/$(DOCKER_ORG)/$(PROJECT_NAME):latest
	@echo "$(GREEN)Docker image pushed!$(RESET)"

docker-scan: ## Docker - Scan Docker image for vulnerabilities
	@echo "$(BLUE)Scanning Docker image for vulnerabilities...$(RESET)"
	@docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
		aquasec/trivy image $(PROJECT_NAME):$(VERSION)

# License Commands
license-validate: ## License - Validate license configuration
	@echo "$(BLUE)Validating license...$(RESET)"
	@python3 scripts/license/validate.py

license-check-features: ## License - Check available features
	@echo "$(BLUE)Checking available features...$(RESET)"
	@python3 scripts/license/check_features.py

# Build Commands
build: docker-build ## Build - Build application

# Clean Commands
clean: ## Clean build artifacts and cache files
	@echo "$(BLUE)Cleaning build artifacts...$(RESET)"
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete
	@find . -type f -name "*.pyo" -delete
	@find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name "htmlcov" -exec rm -rf {} + 2>/dev/null || true
	@rm -f .coverage
	@echo "$(GREEN)Clean complete!$(RESET)"

clean-docker: ## Clean Docker containers and volumes
	@echo "$(BLUE)Cleaning Docker resources...$(RESET)"
	@docker-compose down -v
	@docker system prune -f
	@echo "$(GREEN)Docker resources cleaned!$(RESET)"

# Version Management
version: ## Show current version
	@echo "$(BLUE)Elder version: $(VERSION)$(RESET)"

version-bump-patch: ## Bump patch version
	@./scripts/version/update-version.sh patch
	@echo "$(GREEN)Version bumped to $$(cat .version)$(RESET)"

version-bump-minor: ## Bump minor version
	@./scripts/version/update-version.sh minor
	@echo "$(GREEN)Version bumped to $$(cat .version)$(RESET)"

version-bump-major: ## Bump major version
	@./scripts/version/update-version.sh major
	@echo "$(GREEN)Version bumped to $$(cat .version)$(RESET)"

# Health Checks
health: ## Check health of all services
	@echo "$(BLUE)Checking service health...$(RESET)"
	@echo "$(YELLOW)API:$(RESET)"
	@curl -f http://localhost:5000/healthz && echo " $(GREEN)✓$(RESET)" || echo " $(RED)✗$(RESET)"
	@echo "$(YELLOW)Prometheus:$(RESET)"
	@curl -f http://localhost:9090/-/healthy && echo " $(GREEN)✓$(RESET)" || echo " $(RED)✗$(RESET)"
	@echo "$(YELLOW)Grafana:$(RESET)"
	@curl -f http://localhost:3001/api/health && echo " $(GREEN)✓$(RESET)" || echo " $(RED)✗$(RESET)"

# Status
status: ## Show status of all services
	@echo "$(BLUE)Service Status:$(RESET)"
	@docker-compose ps

# === Kubernetes Deployment (microk8s + Helm v3) ===
HELM_DIR := infrastructure/helm/$(PROJECT_NAME)

k8s-alpha-deploy:
	@./tests/k8s/alpha/run-all-alpha.sh

k8s-beta-deploy:
	@./tests/k8s/beta/run-all-beta.sh

k8s-prod-deploy:
	@read -p "Deploy to PRODUCTION? (yes/NO): " c && [ "$$c" = "yes" ]
	@helm upgrade --install $(PROJECT_NAME) ./$(HELM_DIR) --namespace $(PROJECT_NAME)-prod --create-namespace --values ./$(HELM_DIR)/values.yaml --wait --timeout 10m

k8s-alpha-test:
	@./tests/k8s/alpha/run-all-alpha.sh

k8s-beta-test:
	@./tests/k8s/beta/run-all-beta.sh

k8s-cleanup:
	@helm uninstall $(PROJECT_NAME) -n $(PROJECT_NAME)-alpha 2>/dev/null || true
	@helm uninstall $(PROJECT_NAME) -n $(PROJECT_NAME)-beta 2>/dev/null || true
	@microk8s kubectl delete namespace $(PROJECT_NAME)-alpha 2>/dev/null || true
	@microk8s kubectl delete namespace $(PROJECT_NAME)-beta 2>/dev/null || true

helm-lint:
	@helm lint ./$(HELM_DIR)

helm-template:
	@helm template $(PROJECT_NAME) ./$(HELM_DIR) --values ./$(HELM_DIR)/values-alpha.yaml
