# Checkpoint 0.1 Verification Guide
**Development Environment Setup - Definition of Done**

This guide walks you through verifying that the development environment is correctly configured and ready for implementation.

---

## Prerequisites Check ✓

Before starting, verify you have:

```bash
# Check Docker Desktop is installed and running
docker --version          # Should output: Docker version 20.10+
docker ps                 # Should list running containers (may be empty)

# Check Docker Compose
docker compose version    # Should output: Docker Compose version 2.x+

# Check Python
python --version          # Should output: Python 3.11+
# or on Windows:
py --version              # Should output: Python 3.11+

# Check Git
git --version             # Should output: git version 2.x+
```

If any fail:
- **Docker missing?** Install Docker Desktop from https://www.docker.com/products/docker-desktop/
- **Python missing?** Install Python 3.11+ from https://www.python.org/
- **Git missing?** Install from https://git-scm.com/

---

## Step 1: Start Docker Services

```bash
# Navigate to project root
cd /path/to/GitHub-AI-Analytics

# Copy environment template
cp .env.example .env

# Start all services
docker compose up -d

# Expected output:
# [+] Running 3/3
#  ✔ Container repo_intelligence_postgres  Started
#  ✔ Container repo_intelligence_redis     Started
#  ✔ Container repo_intelligence_pgadmin   Started
```

**Verification:**
```bash
# Check all containers are healthy
docker compose ps

# Expected output (STATUS column):
# postgres    Up (healthy)
# redis       Up (healthy)  
# pgadmin     Up (healthy)
```

**Why this matters:**
- `postgres`: Primary database for all application data
- `redis`: Caching layer for query results and sessions
- `pgadmin`: Web UI for database management (debugging)

Health checks ensure services are truly ready (not just started).

---

## Step 2: Verify PostgreSQL

```bash
# Option A: Via psql command line
docker compose exec postgres psql -U postgres -d postgres -c "SELECT version();"

# Expected output:
# PostgreSQL 17.x on [OS], compiled by [compiler], xx-bit

# Option B: Via pgAdmin web interface
# Open http://localhost:5050
# Email: admin@admin.com
# Password: admin
# Left panel: Servers → PostgreSQL → Databases → ai_analytics
# Right click database → Properties (should show tables)
```

**Verify Database Initialization:**
```bash
# Connect as application user
docker compose exec postgres psql -U repo_user -d ai_analytics -c "\dt"

# Expected output (lists tables):
#               List of relations
#  Schema |       Name       | Type  | Owner
# --------+------------------+-------+---------
#  public | repositories     | table | repo_user
#  public | technologies     | table | repo_user
#  public | repository_technologies | table | repo_user
# (3 rows)

# Check extensions are installed
docker compose exec postgres psql -U repo_user -d ai_analytics -c "\dx"

# Expected output includes:
#   vector      | ...
#   uuid-ossp   | ...
#   pg_trgm     | ...
#   unaccent    | ...
```

**Why this matters:**
- `repositories` table: Stores repository metadata
- `technologies` table: Reference data (languages, frameworks)
- `repository_technologies` table: Join table (many-to-many relationships)
- Extensions: Support embeddings (vector), IDs (uuid-ossp), search (pg_trgm, unaccent)

---

## Step 3: Verify Redis

```bash
# Connect to Redis
docker compose exec redis redis-cli ping

# Expected output:
# PONG

# Check Redis is accepting connections
docker compose exec redis redis-cli INFO server

# Expected output includes:
# redis_version:7.x
# uptime_in_seconds: [number]

# Test set/get (cache operations)
docker compose exec redis redis-cli SET test-key "Hello, Redis!" EX 60
docker compose exec redis redis-cli GET test-key

# Expected output:
# (first command) OK
# (second command) "Hello, Redis!"

# Clean up test key
docker compose exec redis redis-cli DEL test-key
```

**Why this matters:**
- Redis is the caching layer
- Used for query result caching (performance)
- Used for rate limiting (API protection)
- Used for leaderboards/sorted results

---

## Step 4: Python Environment

```bash
# Create virtual environment
python -m venv venv

# Activate (choose based on your OS):
# Windows (cmd.exe):
venv\Scripts\activate.bat
# Windows (PowerShell):
venv\Scripts\Activate.ps1
# macOS/Linux:
source venv/bin/activate

# Verify activated (should show (venv) in prompt)
# Install dependencies
pip install -r requirements.txt

# Verify installation
pip list | grep -E "psycopg|redis|pytest|python-dotenv"

# Expected output (versions may differ):
# psycopg                   3.2.13
# redis                     5.2.0
# pytest                    8.3.3
# python-dotenv             1.0.1
```

---

## Step 5: Run Tests

```bash
# Run full test suite
pytest tests/ -v --tb=short

# Expected output:
# ===== test session starts =====
# tests/... PASSED
# tests/... PASSED
# ===== X passed in Y.XXs =====

# If tests show SKIPPED for database tests:
# Reason: Docker services not reachable
# Solution: Wait 10-15 seconds, check 'docker compose ps' shows (healthy)

# Run specific test file
pytest tests/conftest.py -v

# Check test coverage (optional)
pytest tests/ --cov=src --cov-report=html
# Opens htmlcov/index.html in browser for detailed coverage
```

**What tests verify:**
- Python environment can import all modules
- PostgreSQL connection works
- Redis connection works
- Database schema is initialized correctly
- Environment variables are loaded properly

---

## Step 6: Access Services

### pgAdmin (Database GUI)
```
URL: http://localhost:5050
Email: admin@admin.com
Password: admin

Navigation:
1. Left sidebar → Servers
2. Click "PostgreSQL" (pre-configured)
3. Click "Databases" → "ai_analytics"
4. Right-click for options (query tool, properties, backup)
```

### Direct Database Connection (psql)
```bash
# As application user (for development)
psql -h localhost -U repo_user -d ai_analytics
# Password: repo_password

# Common commands:
\l                        # List databases
\dt                       # List tables
\d repositories           # Describe table schema
SELECT * FROM repositories LIMIT 5;  # Query data
\q                        # Quit
```

### Redis CLI
```bash
# Connect directly
redis-cli -h localhost -p 6379

# Common commands:
PING                      # Test connection
SET mykey "myvalue"       # Set value
GET mykey                 # Get value
DEL mykey                 # Delete value
FLUSHDB                   # Clear all data (dev only!)
INFO                      # Server info
```

---

## Definition of Done Checklist

Use this checklist to verify Checkpoint 0.1 is complete:

- [ ] **Docker Compose starts all services cleanly**
  - Command: `docker compose up -d`
  - Verification: `docker compose ps` shows all services as "Up (healthy)"
  - Time to start: ~10-15 seconds (first run may take longer for image pulls)

- [ ] **PostgreSQL is running and initialized**
  - Database created: `ai_analytics`
  - Schema initialized: Tables `repositories`, `technologies`, `repository_technologies` exist
  - Extensions installed: `vector`, `uuid-ossp`, `pg_trgm`, `unaccent`
  - Users created: `postgres` (admin), `repo_user` (application)

- [ ] **Redis is running and accessible**
  - Command: `docker compose exec redis redis-cli ping` returns `PONG`
  - Can set and get values
  - Persistence configured (RDB snapshots enabled)

- [ ] **pgAdmin is accessible**
  - URL: `http://localhost:5050`
  - Can login with admin@admin.com / admin
  - PostgreSQL connection pre-configured

- [ ] **Python environment set up**
  - Virtual environment created: `venv/`
  - Dependencies installed: `pip list` shows psycopg, redis, pytest, python-dotenv
  - Can import modules: `python -c "import redis, psycopg"`

- [ ] **Setup takes < 5 minutes for new developer**
  - Clone repo
  - Copy .env
  - `docker compose up -d`
  - `python -m venv venv && source venv/bin/activate`
  - `pip install -r requirements.txt`
  - Done!

- [ ] **Documentation complete**
  - [ ] README.md has setup instructions
  - [ ] .env.example explains all variables
  - [ ] FOLDER_STRUCTURE.md documents project organization
  - [ ] docker-compose.yml has comments explaining each service

- [ ] **Developer can run basic tests**
  - Command: `pytest tests/` passes
  - Tests verify Docker connectivity
  - No critical issues blocking development

---

## Troubleshooting During Verification

### Services fail to start
```bash
# Check logs
docker compose logs postgres  # PostgreSQL logs
docker compose logs redis     # Redis logs

# Common issues:
# Port already in use → Change POSTGRES_PORT or REDIS_PORT in .env
# Insufficient disk space → docker system prune -a
# Memory issues → Increase Docker memory allocation
```

### Can't connect to PostgreSQL
```bash
# Check container is running
docker compose ps postgres

# Check network connectivity
docker compose exec postgres pg_isready

# Check credentials
# Default: postgres/postgres_admin (admin) or repo_user/repo_password (app)

# View connection logs
docker compose logs postgres | tail -20
```

### Tests are skipped
```bash
# Likely cause: Services not healthy yet
# Solution: Wait 15 seconds and retry

# Or check services directly
docker compose ps  # Should show (healthy) status

# If not healthy after 30 seconds:
docker compose down -v
docker compose up -d
# Wait 20 seconds
docker compose ps
```

### Can't activate Python venv
```bash
# Windows cmd.exe
venv\Scripts\activate.bat

# Windows PowerShell (if above fails)
venv\Scripts\Activate.ps1

# If PowerShell error (policy):
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# macOS/Linux
source venv/bin/activate

# Verify activation (should show (venv) in prompt)
```

### Port conflicts
```bash
# Find what's using port 5432 (PostgreSQL)
# Windows:
netstat -ano | findstr :5432

# macOS/Linux:
lsof -i :5432

# Solution: Either kill the process or change port in .env
POSTGRES_PORT=5433  # Use different port
docker compose restart
```

---

## Next Steps

Once verification is complete:

1. **Read the architecture:** Review `SYSTEM_ARCHITECTURE.md` to understand design
2. **Understand the roadmap:** See `IMPLEMENTATION_ROADMAP.md` for what builds next
3. **Explore the structure:** Read `FOLDER_STRUCTURE.md` to understand organization
4. **Start development:** Move to Checkpoint 0.2 (Database Schema & Initialization)

---

## Success Confirmation

When you can run this without errors, everything is working:

```bash
# Test complete setup
docker compose up -d
source venv/bin/activate  # or venv\Scripts\activate on Windows
pytest tests/ -v

# Expected output:
# ===== test session starts =====
# ...
# ===== X passed in Y.XXs =====
```

**You're ready to start development! 🎉**

---

## Shutting Down for the Day

```bash
# Stop containers (data persists)
docker compose stop

# Resume next time
docker compose start

# Or completely clean up (wipe data too)
docker compose down -v
docker compose up -d  # Next day
```

