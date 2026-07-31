# Repository Intelligence Engine

An AI-powered intelligence layer over GitHub's public data — see [`claude.md`](claude.md) for the product vision and [`SYSTEM_ARCHITECTURE.md`](SYSTEM_ARCHITECTURE.md) for the engineering blueprint.

**Project status:** Milestone 0 — Project Foundation (see [`IMPLEMENTATION_ROADMAP.md`](IMPLEMENTATION_ROADMAP.md) for full progress tracking).

## Foundational Documents

Read in this order before contributing:

1. [`claude.md`](claude.md) — Product vision and brainstorming
2. [`REPOSITORY_INTELLIGENCE_ENGINE.md`](REPOSITORY_INTELLIGENCE_ENGINE.md) — Data science / research foundation
3. [`TECHNICAL_IMPLEMENTATION_IDEAS.md`](TECHNICAL_IMPLEMENTATION_IDEAS.md) — Engineering research and tradeoffs
4. [`SYSTEM_ARCHITECTURE.md`](SYSTEM_ARCHITECTURE.md) — Implementation blueprint
5. [`IMPLEMENTATION_ROADMAP.md`](IMPLEMENTATION_ROADMAP.md) — Milestone-by-milestone build checklist

These documents are frozen. Implementation follows them; it does not redesign them.

## Local Development Setup

### Prerequisites

**Required:**
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (version 4.0+, includes Docker Compose)
  - **Windows 11 Home**: Use WSL 2 backend (default in new installs)
  - **macOS**: Apple Silicon or Intel-based
  - **Linux**: Install Docker Engine and Docker Compose separately
- Python 3.11+ (verify: `python --version` or `py --version` on Windows)
- Git (verify: `git --version`)
- 4GB free disk space minimum
- ~30-40 seconds startup time (first pull of images)

**Highly Recommended:**
- [pgAdmin 4](https://www.pgadmin.org/) — Browser-based database GUI (already included in Docker)
- [DBeaver](https://dbeaver.io/) or [DataGrip](https://www.jetbrains.com/datagrip/) — Desktop database client
- [Redis Desktop Manager](https://resp.app/) — Redis GUI (optional)

### Quick Start (5 Minutes)

```bash
# 1. Clone repository and enter project
git clone <repo-url>
cd GitHub-AI-Analytics

# 2. Copy environment template (uses safe defaults)
cp .env.example .env
# Edit .env only if you need non-default ports or credentials

# 3. Start Docker services (PostgreSQL, Redis, pgAdmin)
docker compose up -d

# Verify all services are healthy
docker compose ps
# Should show postgres, redis, pgadmin with STATUS "Up (healthy)"

# 4. Create Python virtual environment
python -m venv venv

# 5. Activate virtual environment
# On Windows (cmd.exe):
venv\Scripts\activate.bat
# Or PowerShell:
venv\Scripts\Activate.ps1
# On macOS/Linux:
source venv/bin/activate

# 6. Install Python dependencies
pip install -r requirements.txt

# 7. Verify environment (runs tests that check Docker connectivity)
pytest tests/ -v

# If you see "PASSED" for database tests: ✓ Setup complete!
# If tests are "SKIPPED": Services not ready yet (wait 10 seconds, retry)
```

**Expected setup time:** 3-5 minutes (including Docker image pulls on first run)

### Accessing Services

Once `docker compose up -d` completes and health checks pass:

**PostgreSQL**
- **Host:** `localhost:5432` (or `127.0.0.1:5432`)
- **Admin User:** `postgres` / `postgres_admin`
- **App User:** `repo_user` / `repo_password`
- **Database:** `ai_analytics`
- **Connection String:** `postgresql://repo_user:repo_password@localhost:5432/ai_analytics`

**pgAdmin (Web UI)**
- **URL:** `http://localhost:5050`
- **Email:** `admin@admin.com`
- **Password:** `admin`
- **PostgreSQL connection:** Pre-configured (Server → Databases → ai_analytics)

**Redis**
- **Host:** `localhost:6379`
- **No authentication** (default for local dev)
- **Tools:** redis-cli, or access via Python `redis.Redis(host='localhost')`

### Database Operations

**Connect via psql (command line)**
```bash
# Connect as application user
psql -h localhost -U repo_user -d ai_analytics
# Password: repo_password

# Common commands:
\dt                          # List tables
\d repositories              # Describe table schema
SELECT COUNT(*) FROM repositories;  # Query data
\q                           # Quit
```

**Reset Database (Clear All Data)**
```bash
# Option 1: Stop and delete volumes (complete reset)
docker compose down -v
docker compose up -d

# Option 2: Clear via psql (keep schema, remove data)
psql -h localhost -U repo_user -d ai_analytics
DROP TABLE IF EXISTS repository_technologies CASCADE;
DROP TABLE IF EXISTS repositories CASCADE;
DROP TABLE IF EXISTS technologies CASCADE;
\q
```

**Backup Database**
```bash
# Export to SQL file
pg_dump -h localhost -U repo_user -d ai_analytics > backup.sql

# Restore from SQL file
psql -h localhost -U repo_user -d ai_analytics < backup.sql
```

**View Logs**
```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f postgres      # PostgreSQL
docker compose logs -f redis         # Redis
docker compose logs -f pgadmin       # pgAdmin

# Stop following logs: Ctrl+C
```

### Stopping and Restarting

```bash
# Stop containers (data persists in volumes)
docker compose stop

# Start containers again
docker compose start

# Stop and remove containers (keep data volumes)
docker compose down

# Stop and remove EVERYTHING (including data!)
docker compose down -v

# View container status
docker compose ps

# View volumes (where data is stored)
docker volume ls
```

### Troubleshooting

| Symptom | Likely Cause | Solution |
|---------|--------------|----------|
| `docker compose: command not found` | Docker not installed or not in PATH | Install Docker Desktop or add Docker to PATH |
| Docker Desktop won't start | WSL 2 issues (Windows) | Reinstall WSL 2: `wsl --install` |
| `Container failed to start` | Port already in use | Check `lsof -i:5432` (or change POSTGRES_PORT in .env) |
| PostgreSQL won't connect | Container not healthy yet | Wait 10-15 seconds, check `docker compose ps` |
| Tests pass but DB tests skipped | Services not reachable | Ensure `docker compose ps` shows all services as "Up (healthy)" |
| Permission denied errors (Linux) | Docker group permissions | Add user to docker group: `sudo usermod -aG docker $USER` |
| `pg_isready: command not found` | Inside container without psql installed | Use `docker compose exec postgres pg_isready` instead |
| Out of disk space | Docker images/volumes accumulating | Run `docker system prune -a` to clean up unused resources |

### Development Workflow

```bash
# 1. Start day
docker compose up -d

# 2. Activate Python environment
source venv/bin/activate  # (or venv\Scripts\activate on Windows)

# 3. Work on code
# ... edit files in src/ ...

# 4. Test your changes
pytest tests/ -v

# 5. View database changes (optional)
# Open http://localhost:5050 → pgAdmin → Databases → ai_analytics

# 6. End of day
docker compose down  # (data persists in volumes)

# 7. Next day
docker compose up -d  # Restart containers, data restored
```

## Project Structure

**See [`FOLDER_STRUCTURE.md`](FOLDER_STRUCTURE.md) for complete documentation of each directory.**

Quick overview:
```
├── src/                 # Application modules (acquisition, processing, features, intelligence, etc.)
├── tests/               # Unit, integration, and end-to-end tests
├── data/                # Local data directory (git-ignored)
├── scripts/             # Database initialization and utility scripts
├── notebooks/           # Jupyter notebooks for exploration
├── docs/                # User and developer documentation
├── docker-compose.yml   # Local development infrastructure (PostgreSQL, Redis, pgAdmin)
├── .env.example         # Environment variables template (documented)
├── requirements.txt     # Python dependencies
└── FOLDER_STRUCTURE.md  # Detailed folder organization and principles
```

**Key Principles:**
1. **Modular Monolith:** Each `src/` subdirectory is a self-contained module with clear responsibilities
2. **Data Flow:** Acquisition → Processing → Features → Intelligence → Search/Recommendations → API
3. **No Circular Dependencies:** Lower-level modules never import from higher levels
4. **Testability:** Each module can be tested in isolation with fixtures
5. **Scalability:** Modular structure enables migration to microservices later if needed

## Contributing

Development follows **[`IMPLEMENTATION_ROADMAP.md`](IMPLEMENTATION_ROADMAP.md)** strictly:

- **One checkpoint at a time, in order**
- **Definition of Done verified before moving to next checkpoint**
- **Planning documents are frozen** — implementation executes them, not redesigns them
- **Infrastructure before features** — foundation first, applications later

**Current Status:** Checkpoint 0.1 (Development Environment Setup) ✓ Complete
