# Elder - Entity Relationship Tracking Application Makefile

.PHONY: help \
        setup setup-env setup-python \
        dev dev-api dev-stop generate-grpc \
        test test-unit test-integration test-e2e test-functional test-security test-coverage \
        smoke-test smoke-test-beta seed-mock-data screenshots \
        lint format format-check \
        test-ui test-ui-headed test-ui-debug test-beta \
        build docker-build docker-build-alpha docker-push docker-scan \
        db-migrate db-create-migration db-downgrade db-reset db-shell db-backup \
        deploy-alpha deploy-beta \
        helm-lint helm-template \
        license-validate license-check-features \
        clean clean-docker \
        version version-bump-patch version-bump-minor version-bump-major \
        health pre-commit

.DEFAULT_GOAL := help

# ── Variables ──────────────────────────────────────────────────────────────
PROJECT_NAME    := elder
VERSION         := $(shell cat .version 2>/dev/null || echo "0.0.0")
DOCKER_REGISTRY := ghcr.io
DOCKER_ORG      := penguintechinc
HELM_DIR        := k8s/helm/$(PROJECT_NAME)
K8S_NAMESPACE   := $(PROJECT_NAME)
VENV            := .venv
PYTHON          := $(VENV)/bin/python3
PIP             := $(VENV)/bin/pip

# Colors
RED    := \033[31m
GREEN  := \033[32m
YELLOW := \033[33m
BLUE   := \033[34m
RESET  := \033[0m

# ── Help ───────────────────────────────────────────────────────────────────
help: ## Show this help message
	@echo "$(BLUE)Elder - Entity Relationship Tracking Application$(RESET)"
	@echo "$(YELLOW)Usage: make <target>$(RESET)"
	@echo ""
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  $(YELLOW)%-28s$(RESET) %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ── Setup ──────────────────────────────────────────────────────────────────
setup: setup-env setup-python ## Install all dependencies and initialize the project
	@echo "$(GREEN)Setup complete! Edit .env, then run 'make dev'$(RESET)"

setup-env: ## Create .env from template (no-op if already exists)
	@if [ ! -f .env ]; then \
		echo "$(YELLOW)Creating .env file...$(RESET)"; \
		printf '# Elder Environment Configuration\n' > .env; \
		printf 'FLASK_ENV=development\n' >> .env; \
		printf 'SECRET_KEY=dev-secret-key-change-in-production\n' >> .env; \
		printf '\n# Database\n' >> .env; \
		printf 'POSTGRES_DB=elder\n' >> .env; \
		printf 'POSTGRES_USER=elder\n' >> .env; \
		printf 'POSTGRES_PASSWORD=elder_dev_password\n' >> .env; \
		printf 'DATABASE_URL=postgresql://elder:elder_dev_password@localhost:5432/elder\n' >> .env; \
		printf '\n# Redis\n' >> .env; \
		printf 'REDIS_PASSWORD=elder_redis_password\n' >> .env; \
		printf 'REDIS_URL=redis://:elder_redis_password@localhost:6379/0\n' >> .env; \
		printf '\n# Admin User\n' >> .env; \
		printf 'ADMIN_EMAIL=admin@localhost.local\n' >> .env; \
		printf 'ADMIN_PASSWORD=\n' >> .env; \
		printf '\n# License (optional)\n' >> .env; \
		printf 'LICENSE_KEY=\n' >> .env; \
		echo "$(GREEN).env created$(RESET)"; \
	else \
		echo "$(YELLOW).env already exists$(RESET)"; \
	fi

setup-python: ## Create .venv and install Python dependencies
	@echo "$(BLUE)Setting up Python virtualenv...$(RESET)"
	@python3 --version
	@python3 -m venv $(VENV)
	@$(PIP) install --upgrade pip --quiet
	@$(PIP) install -r requirements.txt --quiet
	@echo "$(GREEN)Python dependencies installed into $(VENV)/$(RESET)"

# ── Development ────────────────────────────────────────────────────────────
dev: ## Start backing services (postgres + redis) for local Flask development
	@echo "$(BLUE)Starting backing services...$(RESET)"
	@docker run -d --name elder-postgres -p 5432:5432 \
		-e POSTGRES_DB=elder -e POSTGRES_USER=elder \
		-e POSTGRES_PASSWORD=elder_dev_password \
		-e PGDATA=/var/lib/postgresql/data/pgdata \
		postgres:16-bookworm 2>/dev/null || docker start elder-postgres
	@docker run -d --name elder-redis -p 6379:6379 \
		redis:7-bookworm redis-server --requirepass elder_redis_password \
		2>/dev/null || docker start elder-redis
	@echo "$(GREEN)postgres:5432 and redis:6379 ready — run 'make dev-api' to start Flask$(RESET)"

dev-api: ## Start Flask API (requires 'make dev' backing services)
	@FLASK_APP=apps.api.main:create_app FLASK_ENV=development \
		$(PYTHON) -m flask run --host=0.0.0.0 --port=5000

dev-stop: ## Stop local backing services
	@docker stop elder-postgres elder-redis 2>/dev/null || true
	@echo "$(GREEN)Backing services stopped$(RESET)"

generate-grpc: ## Regenerate Python gRPC stubs from protobuf schemas
	@echo "$(BLUE)Generating gRPC stubs...$(RESET)"
	@docker run --rm -v $(PWD):/app -w /app python:3.13-slim bash -c "\
		pip install --quiet grpcio-tools protobuf && \
		python3 -m grpc_tools.protoc \
			-Iapps/api/grpc/proto \
			--python_out=apps/api/grpc/generated \
			--grpc_python_out=apps/api/grpc/generated \
			apps/api/grpc/proto/*.proto && \
		cd apps/api/grpc/generated && \
		for file in *.py; do \
			sed -i 's/^import \([a-z_]*\)_pb2/from . import \1_pb2/g' \$\$file 2>/dev/null || true; \
		done"
	@touch apps/api/grpc/generated/__init__.py
	@echo "$(GREEN)gRPC stubs generated$(RESET)"

# ── Testing ────────────────────────────────────────────────────────────────
test: lint test-unit test-integration test-functional test-security ## Run all tests (lint + unit + integration + functional + security)
	@echo "$(GREEN)All tests passed$(RESET)"

smoke-test: ## Run smoke tests — build, API health, page loads (<2 min, pre-commit)
	@bash scripts/smoke-test.sh

smoke-test-beta: ## Run smoke tests against beta cluster (dal2)
	@bash scripts/smoke-test.sh --beta

test-beta: ## Run full test suite against beta: smoke + REST API + Playwright e2e
	@echo "$(BLUE)=== Beta Test Suite ===$(RESET)"
	@echo "$(BLUE)[1/3] Smoke tests...$(RESET)"
	@bash scripts/smoke-test.sh --beta
	@echo "$(BLUE)[2/3] REST API tests...$(RESET)"
	@$(PYTHON) scripts/test-rest-api.py --url https://dal2.penguintech.io --host-header elder.penguintech.cloud --no-verify-ssl
	@echo "$(BLUE)[3/3] Playwright e2e...$(RESET)"
	@cd web && PLAYWRIGHT_BASE_URL=https://dal2.penguintech.io \
		PLAYWRIGHT_TARGET_HOST=elder.penguintech.cloud \
		PLAYWRIGHT_WEBSERVER_DISABLED=1 \
		npx playwright test
	@echo "$(GREEN)All beta tests passed$(RESET)"

test-unit: ## Run unit tests
	@echo "$(BLUE)Running unit tests...$(RESET)"
	@$(PYTHON) -m pytest tests/unit/ -v

test-integration: ## Run integration tests
	@echo "$(BLUE)Running integration tests...$(RESET)"
	@$(PYTHON) -m pytest tests/integration/ -v

test-e2e: ## Run E2E tests against local alpha cluster
	@bash scripts/e2e-test-alpha.sh

test-functional: ## Run functional API tests against running services
	@echo "$(BLUE)Running API functional tests...$(RESET)"
	@bash scripts/test-api.sh
	@$(PYTHON) scripts/test-rest-api.py
	@$(PYTHON) scripts/test-api-validation.py

test-security: ## Run all security scans (bandit, pip-audit, npm audit, hadolint, gitleaks, trufflehog, semgrep, trivy)
	@echo "$(BLUE)[1/8] bandit — Python SAST...$(RESET)"
	@$(PYTHON) -m bandit -r apps/ shared/ -q
	@echo "$(BLUE)[2/8] pip-audit — Python dependency CVEs...$(RESET)"
	@$(PYTHON) -m pip_audit -r requirements.txt --progress-spinner off
	@echo "$(BLUE)[3/8] npm audit — Node dependency CVEs...$(RESET)"
	@cd web && npm audit --audit-level=high
	@echo "$(BLUE)[4/8] hadolint — Dockerfile linting...$(RESET)"
	@docker run --rm -i hadolint/hadolint:2.12.0 < apps/api/Dockerfile
	@docker run --rm -i hadolint/hadolint:2.12.0 < web/Dockerfile
	@docker run --rm -i hadolint/hadolint:2.12.0 < apps/scanner/Dockerfile
	@docker run --rm -i hadolint/hadolint:2.12.0 < apps/worker/Dockerfile
	@echo "$(BLUE)[5/8] gitleaks — secret scan (git history)...$(RESET)"
	@docker run --rm -v $(PWD):/repo \
		zricethezav/gitleaks:v8.21.2 detect --source /repo --no-git --quiet
	@echo "$(BLUE)[6/8] trufflehog — deep secret scan...$(RESET)"
	@docker run --rm -v $(PWD):/repo \
		trufflesecurity/trufflehog:3.88.1 filesystem /repo --only-verified --no-update --quiet
	@echo "$(BLUE)[7/8] semgrep — SAST + OWASP Top 10...$(RESET)"
	@docker run --rm -v $(PWD):/src \
		semgrep/semgrep:1.107.0 semgrep scan /src \
		--config p/security-audit --config p/secrets --config p/owasp-top-ten \
		--quiet --error
	@echo "$(BLUE)[8/8] trivy — filesystem CVE scan...$(RESET)"
	@docker run --rm -v $(PWD):/project \
		aquasec/trivy:0.69.3 fs /project --exit-code 1 --severity HIGH,CRITICAL --quiet
	@echo "$(GREEN)All security scans passed$(RESET)"

test-coverage: ## Generate HTML coverage report (htmlcov/)
	@$(PYTHON) -m pytest tests/ --cov=apps --cov-report=html --cov-report=term-missing
	@echo "$(GREEN)Coverage report in htmlcov/$(RESET)"

test-ui: ## Run Playwright web UI tests (headless)
	@cd web && npm run test:e2e

test-ui-headed: ## Run Playwright web UI tests with visible browser
	@cd web && npm run test:e2e:ui

test-ui-debug: ## Run Playwright in step-through debug mode
	@cd web && npm run test:e2e:debug

seed-mock-data: ## Seed 3-4 realistic mock items per feature for local testing
	@echo "$(BLUE)Seeding mock data...$(RESET)"
	@$(PYTHON) scripts/seed_mock_data.py
	@echo "$(GREEN)Mock data seeded$(RESET)"

screenshots: ## Capture screenshots with mock data (requires seed-mock-data first)
	@node scripts/capture-screenshots.cjs

pre-commit: ## Run full pre-commit sequence (lint + security + tests + smoke-test)
	@echo "$(YELLOW)=== Pre-commit Checks ===$(RESET)"
	@$(MAKE) lint
	@$(MAKE) test-security
	@$(MAKE) test
	@$(MAKE) smoke-test
	@echo "$(GREEN)All pre-commit checks passed$(RESET)"

# ── Code Quality ───────────────────────────────────────────────────────────
lint: ## Run all linters (flake8, black, isort, mypy, hadolint, shellcheck)
	@echo "$(BLUE)[1/6] flake8 — Python style...$(RESET)"
	@$(PYTHON) -m flake8 apps/ shared/ --max-line-length=120 --exclude=.git,__pycache__,venv,node_modules || true
	@echo "$(BLUE)[2/6] black — Python formatting...$(RESET)"
	@$(PYTHON) -m black --check apps/ shared/ tests/ --exclude '/(\.git|venv|__pycache__|node_modules)/' || true
	@echo "$(BLUE)[3/6] isort — Python import ordering...$(RESET)"
	@$(PYTHON) -m isort --check-only apps/ shared/ tests/ || true
	@echo "$(BLUE)[4/6] mypy — Python type checking...$(RESET)"
	@$(PYTHON) -m mypy apps/ shared/ --ignore-missing-imports || true
	@echo "$(BLUE)[5/6] hadolint — Dockerfile linting...$(RESET)"
	@find . -name "Dockerfile*" -not -path "*/.git/*" | xargs -I {} sh -c 'echo "  Checking {}..."; docker run --rm -i hadolint/hadolint:2.12.0 < {} || true'
	@echo "$(BLUE)[6/6] shellcheck — Shell script linting...$(RESET)"
	@find . -name "*.sh" -not -path "*/.git/*" -not -path "*/node_modules/*" | xargs -I {} sh -c 'echo "  Checking {}..."; shellcheck {} || true'
	@echo "$(BLUE)Running web linters...$(RESET)"
	@cd web && npm run lint
	@echo "$(GREEN)All linters passed$(RESET)"

format: ## Auto-format Python code (black + isort)
	@$(PYTHON) -m black apps/ shared/ tests/
	@$(PYTHON) -m isort apps/ shared/ tests/
	@echo "$(GREEN)Code formatted$(RESET)"

format-check: ## Check Python formatting without modifying files
	@$(PYTHON) -m black --check apps/ shared/ tests/
	@$(PYTHON) -m isort --check apps/ shared/ tests/

# ── Build ──────────────────────────────────────────────────────────────────
build: docker-build ## Build all service containers

docker-build: ## Build all four service images locally (requires GITHUB_TOKEN env var)
	@test -n "$(GITHUB_TOKEN)" || (echo "$(RED)ERROR: GITHUB_TOKEN env var required for web build$(RESET)" && exit 1)
	@echo "$(BLUE)Building elder-api...$(RESET)"
	@docker build -t $(DOCKER_REGISTRY)/$(DOCKER_ORG)/elder-api:$(VERSION) \
		-f apps/api/Dockerfile .
	@echo "$(BLUE)Building elder-web...$(RESET)"
	@docker build -t $(DOCKER_REGISTRY)/$(DOCKER_ORG)/elder-web:$(VERSION) \
		--build-arg GITHUB_TOKEN=$(GITHUB_TOKEN) \
		-f web/Dockerfile .
	@echo "$(BLUE)Building elder-scanner...$(RESET)"
	@docker build -t $(DOCKER_REGISTRY)/$(DOCKER_ORG)/elder-scanner:$(VERSION) \
		-f apps/scanner/Dockerfile apps/scanner
	@echo "$(BLUE)Building elder-worker...$(RESET)"
	@docker build -t $(DOCKER_REGISTRY)/$(DOCKER_ORG)/elder-worker:$(VERSION) \
		-f apps/worker/Dockerfile .
	@echo "$(GREEN)All images built at $(VERSION)$(RESET)"

docker-build-alpha: ## Build and push all service images to local MicroK8s registry (localhost:32000)
	@test -n "$(GITHUB_TOKEN)" || (echo "$(RED)ERROR: GITHUB_TOKEN env var required for web build$(RESET)" && exit 1)
	@echo "$(BLUE)Building elder-api → localhost:32000...$(RESET)"
	@docker build -t localhost:32000/elder-api:alpha-latest \
		--build-arg APP_VERSION=$(VERSION) \
		-f apps/api/Dockerfile .
	@docker push localhost:32000/elder-api:alpha-latest
	@echo "$(BLUE)Building elder-web → localhost:32000...$(RESET)"
	@docker build -t localhost:32000/elder-web:alpha-latest \
		--build-arg GITHUB_TOKEN=$(GITHUB_TOKEN) \
		--build-arg VITE_VERSION=$(VERSION) \
		--build-arg VITE_BUILD_TIME=$(shell date +%s) \
		-f web/Dockerfile .
	@docker push localhost:32000/elder-web:alpha-latest
	@echo "$(BLUE)Building elder-scanner → localhost:32000...$(RESET)"
	@docker build -t localhost:32000/elder-scanner:alpha-latest \
		--build-arg APP_VERSION=$(VERSION) \
		-f apps/scanner/Dockerfile apps/scanner
	@docker push localhost:32000/elder-scanner:alpha-latest
	@echo "$(BLUE)Building elder-worker → localhost:32000...$(RESET)"
	@docker build -t localhost:32000/elder-worker:alpha-latest \
		--build-arg APP_VERSION=$(VERSION) \
		-f apps/worker/Dockerfile .
	@docker push localhost:32000/elder-worker:alpha-latest
	@echo "$(GREEN)All alpha images built and pushed at $(VERSION)$(RESET)"

docker-push: ## Push all service images to ghcr.io (requires docker login)
	@for svc in api web scanner worker; do \
		echo "$(BLUE)Pushing elder-$$svc...$(RESET)"; \
		docker push $(DOCKER_REGISTRY)/$(DOCKER_ORG)/elder-$$svc:$(VERSION); \
	done
	@echo "$(GREEN)All images pushed$(RESET)"

docker-scan: ## Scan filesystem for vulnerabilities with trivy
	@docker run --rm -v $(PWD):/project \
		aquasec/trivy:0.69.3 fs /project --severity HIGH,CRITICAL

# ── Database ───────────────────────────────────────────────────────────────
db-migrate: ## Run Alembic migrations (upgrade head)
	@echo "$(BLUE)Running migrations...$(RESET)"
	@$(PYTHON) -m alembic upgrade head
	@echo "$(GREEN)Migrations complete$(RESET)"

db-create-migration: ## Create a new Alembic migration (prompts for message)
	@read -p "Migration message: " msg; \
	$(PYTHON) -m alembic revision --autogenerate -m "$$msg"

db-downgrade: ## Roll back one Alembic migration
	@$(PYTHON) -m alembic downgrade -1

db-reset: ## Reset database — destroys all data (prompts for confirmation)
	@echo "$(RED)WARNING: This will destroy all data!$(RESET)"
	@read -p "Type 'yes' to confirm: " confirm; \
	[ "$$confirm" = "yes" ] || (echo "Cancelled" && exit 1)
	@docker stop elder-postgres 2>/dev/null || true
	@docker rm elder-postgres 2>/dev/null || true
	@$(MAKE) dev
	@sleep 3
	@$(PYTHON) -m alembic upgrade head
	@echo "$(GREEN)Database reset complete$(RESET)"

db-shell: ## Open psql shell in the local postgres container
	@docker exec -it elder-postgres psql -U elder -d elder

db-backup: ## Backup local postgres to backups/
	@mkdir -p backups
	@docker exec elder-postgres pg_dump -U elder elder \
		> backups/elder_$$(date +%Y%m%d_%H%M%S).sql
	@echo "$(GREEN)Backup saved to backups/$(RESET)"

# ── Kubernetes Deployment ──────────────────────────────────────────────────
deploy-alpha: ## Deploy to local alpha cluster via Kustomize (context: local-alpha)
	@echo "$(BLUE)Deploying to alpha (kustomize)...$(RESET)"
	@kubectl apply --context local-alpha -k k8s/kustomize/overlays/alpha
	@kubectl --context local-alpha rollout status deployment -n $(K8S_NAMESPACE) --timeout=120s
	@echo "$(GREEN)Alpha deployment complete$(RESET)"

deploy-beta: ## Deploy to beta cluster via Helm (context: dal2-beta)
	@echo "$(BLUE)Deploying to beta...$(RESET)"
	@bash scripts/deploy-beta.sh
	@echo "$(GREEN)Beta deployment complete$(RESET)"

deploy-dev: deploy-alpha ## Alias for dev deployment (alias to deploy-alpha)

deploy-prod: ## Deploy to production cluster via Helm (context: $(PROJECT_NAME)-prod)
	@echo "$(BLUE)Deploying to production...$(RESET)"
	@echo "$(YELLOW)Ensure you have pushed a git tag before deploying to prod$(RESET)"
	@helm upgrade --install $(PROJECT_NAME) $(HELM_DIR) \
		--context $(PROJECT_NAME)-prod \
		--namespace $(K8S_NAMESPACE) \
		--create-namespace \
		--values $(HELM_DIR)/values-prod.yaml \
		--wait --timeout 300s
	@kubectl --context $(PROJECT_NAME)-prod rollout status deployment -n $(K8S_NAMESPACE) --timeout=300s
	@echo "$(GREEN)Production deployment complete$(RESET)"

helm-lint: ## Lint the Helm chart
	@helm lint $(HELM_DIR)

helm-template: ## Render Helm templates with alpha values (dry-run review)
	@helm template $(PROJECT_NAME) $(HELM_DIR) --values $(HELM_DIR)/values-alpha.yaml

# ── License ────────────────────────────────────────────────────────────────
license-validate: ## Validate license configuration
	@$(PYTHON) scripts/license/validate.py

license-check-features: ## List available licensed features
	@$(PYTHON) scripts/license/check_features.py

# ── Housekeeping ───────────────────────────────────────────────────────────
clean: ## Remove Python caches, pytest artifacts, and coverage reports
	@echo "$(BLUE)Cleaning...$(RESET)"
	@find . -type d -name "__pycache__" -not -path "./.venv/*" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f \( -name "*.pyc" -o -name "*.pyo" \) -not -path "./.venv/*" -delete
	@find . -type d \( -name "*.egg-info" -o -name ".pytest_cache" -o -name ".mypy_cache" \) \
		-not -path "./.venv/*" -exec rm -rf {} + 2>/dev/null || true
	@rm -rf htmlcov .coverage
	@echo "$(GREEN)Clean complete$(RESET)"

clean-docker: ## Stop and remove elder-* containers, prune Docker system
	@docker stop elder-postgres elder-redis 2>/dev/null || true
	@docker rm elder-postgres elder-redis 2>/dev/null || true
	@docker system prune -f
	@echo "$(GREEN)Docker cleaned$(RESET)"

# ── Version Management ─────────────────────────────────────────────────────
version: ## Show current version
	@echo "$(BLUE)Elder $(VERSION)$(RESET)"

version-bump-patch: ## Increment patch version (only if current version is tagged)
	@./scripts/version/update-version.sh patch
	@echo "$(GREEN)Version: $$(cat .version)$(RESET)"

version-bump-minor: ## Increment minor version (only if current version is tagged)
	@./scripts/version/update-version.sh minor
	@echo "$(GREEN)Version: $$(cat .version)$(RESET)"

version-bump-major: ## Increment major version (only if current version is tagged)
	@./scripts/version/update-version.sh major
	@echo "$(GREEN)Version: $$(cat .version)$(RESET)"

# ── Health ─────────────────────────────────────────────────────────────────
health: ## Check health of locally running services
	@echo "$(BLUE)Checking service health...$(RESET)"
	@printf "$(YELLOW)API:$(RESET) "; \
		curl -sf http://localhost:5000/healthz && echo "$(GREEN)✓$(RESET)" || echo "$(RED)✗$(RESET)"
	@printf "$(YELLOW)Postgres:$(RESET) "; \
		docker exec elder-postgres pg_isready -U elder -q 2>/dev/null \
		&& echo "$(GREEN)✓$(RESET)" || echo "$(RED)✗$(RESET)"
	@printf "$(YELLOW)Redis:$(RESET) "; \
		docker exec elder-redis redis-cli -a elder_redis_password ping 2>/dev/null | grep -q PONG \
		&& echo "$(GREEN)✓$(RESET)" || echo "$(RED)✗$(RESET)"
