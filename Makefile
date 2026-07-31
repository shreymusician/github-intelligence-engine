.PHONY: help up down logs db-shell db-reset redis-shell restart clean test

# Colors for output
BLUE := \033[0;34m
GREEN := \033[0;32m
YELLOW := \033[0;33m
NC := \033[0m # No Color

# ============================================================================
# HELP - Display available commands
# ============================================================================
help:
	@echo "$(BLUE)Repository Intelligence Engine - Development Commands$(NC)"
	@echo ""
	@echo "$(GREEN)Infrastructure:$(NC)"
	@echo "  make up              Start all services (PostgreSQL, Redis, pgAdmin)"
	@echo "  make down            Stop all services (data persists)"
	@echo "  make down-clean      Stop all services and delete volumes (wipes data)"
	@echo "  make restart         Restart all services"
	@echo "  make logs            Stream logs from all services"
	@echo ""
	@echo "$(GREEN)Database:$(NC)"
	@echo "  make db-shell        Connect to PostgreSQL with psql"
	@echo "  make db-reset        Drop all data and restart database"
	@echo "  make db-backup       Backup database to backup.sql"
	@echo ""
	@echo "$(GREEN)Cache:$(NC)"
	@echo "  make redis-shell     Connect to Redis CLI"
	@echo "  make redis-flush     Clear all Redis data"
	@echo ""
	@echo "$(GREEN)Development:$(NC)"
	@echo "  make venv            Create Python virtual environment"
	@echo "  make install         Install Python dependencies"
	@echo "  make test            Run test suite"
	@echo "  make clean           Remove Python cache and build artifacts"
	@echo ""
	@echo "$(YELLOW)Note: Services default to localhost:$(NC)"
	@echo "  - PostgreSQL: localhost:5432"
	@echo "  - Redis: localhost:6379"
	@echo "  - pgAdmin: http://localhost:5050"
	@echo ""

# ============================================================================
# INFRASTRUCTURE - Docker Compose operations
# ============================================================================

up:
	@echo "$(BLUE)Starting services...$(NC)"
	docker compose up -d
	@echo "$(GREEN)✓ Services started$(NC)"
	@echo "  PostgreSQL: localhost:5432"
	@echo "  Redis: localhost:6379"
	@echo "  pgAdmin: http://localhost:5050 (admin@admin.com / admin)"
	@sleep 3
	@echo "$(BLUE)Checking health...$(NC)"
	@docker compose ps

down:
	@echo "$(BLUE)Stopping services (data persists)...$(NC)"
	docker compose down
	@echo "$(GREEN)✓ Services stopped$(NC)"

down-clean:
	@echo "$(YELLOW)WARNING: This will delete all database data!$(NC)"
	docker compose down -v
	@echo "$(GREEN)✓ Services stopped and volumes deleted$(NC)"

restart:
	@echo "$(BLUE)Restarting services...$(NC)"
	docker compose restart
	@sleep 2
	docker compose ps

logs:
	@echo "$(BLUE)Streaming logs (Ctrl+C to exit)...$(NC)"
	docker compose logs -f

# ============================================================================
# DATABASE - PostgreSQL operations
# ============================================================================

db-shell:
	@echo "$(BLUE)Connecting to PostgreSQL...$(NC)"
	@echo "$(YELLOW)Tip: Common commands:$(NC)"
	@echo "  \\dt             - List tables"
	@echo "  \\d tablename    - Describe table"
	@echo "  \\l              - List databases"
	@echo "  \\q              - Quit"
	@echo ""
	docker compose exec postgres psql -U repo_user -d ai_analytics

db-reset:
	@echo "$(YELLOW)WARNING: This will DELETE all database data!$(NC)"
	docker compose down -v
	docker compose up -d
	@sleep 5
	@echo "$(GREEN)✓ Database reset complete$(NC)"

db-backup:
	@echo "$(BLUE)Backing up database to backup.sql...$(NC)"
	docker compose exec postgres pg_dump -U repo_user -d ai_analytics > backup.sql
	@echo "$(GREEN)✓ Backup saved to backup.sql$(NC)"

db-restore:
	@echo "$(BLUE)Restoring database from backup.sql...$(NC)"
	docker compose exec -T postgres psql -U repo_user -d ai_analytics < backup.sql
	@echo "$(GREEN)✓ Database restored$(NC)"

# ============================================================================
# CACHE - Redis operations
# ============================================================================

redis-shell:
	@echo "$(BLUE)Connecting to Redis...$(NC)"
	@echo "$(YELLOW)Tip: Common commands:$(NC)"
	@echo "  PING            - Test connection"
	@echo "  SET key value   - Set value"
	@echo "  GET key         - Get value"
	@echo "  DEL key         - Delete key"
	@echo "  FLUSHDB         - Clear all data"
	@echo "  INFO            - Server info"
	@echo "  EXIT            - Quit"
	@echo ""
	docker compose exec redis redis-cli

redis-flush:
	@echo "$(YELLOW)WARNING: This will DELETE all Redis data!$(NC)"
	docker compose exec redis redis-cli FLUSHDB
	@echo "$(GREEN)✓ Redis data cleared$(NC)"

# ============================================================================
# PYTHON ENVIRONMENT
# ============================================================================

venv:
	@echo "$(BLUE)Creating Python virtual environment...$(NC)"
	python -m venv venv
	@echo "$(GREEN)✓ Virtual environment created$(NC)"
	@echo "$(YELLOW)Activate with:$(NC)"
	@echo "  Windows: venv\\Scripts\\activate.bat"
	@echo "  Unix: source venv/bin/activate"

install:
	@echo "$(BLUE)Installing Python dependencies...$(NC)"
	pip install -r requirements.txt
	@echo "$(GREEN)✓ Dependencies installed$(NC)"

# ============================================================================
# TESTING
# ============================================================================

test:
	@echo "$(BLUE)Running test suite...$(NC)"
	pytest tests/ -v --tb=short
	@echo "$(GREEN)✓ Tests complete$(NC)"

test-coverage:
	@echo "$(BLUE)Running tests with coverage...$(NC)"
	pytest tests/ --cov=src --cov-report=html
	@echo "$(GREEN)✓ Coverage report: htmlcov/index.html$(NC)"

# ============================================================================
# CLEANUP
# ============================================================================

clean:
	@echo "$(BLUE)Cleaning Python cache and build artifacts...$(NC)"
	find . -type f -name '*.pyc' -delete
	find . -type d -name '__pycache__' -delete
	find . -type d -name '.pytest_cache' -delete
	find . -type d -name '.mypy_cache' -delete
	find . -type d -name '*.egg-info' -delete
	find . -type d -name '.coverage' -delete
	find . -type d -name 'htmlcov' -delete
	@echo "$(GREEN)✓ Cleanup complete$(NC)"

# ============================================================================
# COMBINED WORKFLOWS
# ============================================================================

# Development setup: create venv, install dependencies, start services
dev-setup: venv install up
	@echo "$(GREEN)✓ Development environment ready!$(NC)"

# Fresh start: wipe everything and start clean
fresh-start: down-clean clean venv install up
	@echo "$(GREEN)✓ Fresh development environment ready!$(NC)"

# Typical day: activate venv, start services
morning:
	@echo "$(BLUE)Good morning! Starting development environment...$(NC)"
	docker compose up -d
	@sleep 2
	docker compose ps
	@echo ""
	@echo "$(GREEN)✓ Services running$(NC)"
	@echo "$(YELLOW)Next: activate venv (Windows: venv\\Scripts\\activate.bat, Unix: source venv/bin/activate)$(NC)"

# End of day: stop services, save work
evening:
	@echo "$(BLUE)Wrapping up...$(NC)"
	docker compose down
	@echo "$(GREEN)✓ Services stopped$(NC)"
	@echo "$(YELLOW)Your data is persisted in volumes and will be restored tomorrow.$(NC)"

# ============================================================================
# ENVIRONMENT INFO
# ============================================================================

status:
	@echo "$(BLUE)Service Status:$(NC)"
	docker compose ps
	@echo ""
	@echo "$(BLUE)Python Environment:$(NC)"
	@python --version
	@echo ""
	@echo "$(BLUE)Docker Version:$(NC)"
	@docker --version

.DEFAULT_GOAL := help
