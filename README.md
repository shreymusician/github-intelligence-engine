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

**Prerequisites**
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (with Compose v2)
- Python 3.11+ (verify with `py --version` on Windows or `python3 --version` on macOS/Linux)
- Git

**Setup steps**

```bash
# 1. Clone and enter the project
git clone <repo-url>
cd GitHub-AI-Analytics

# 2. Copy environment configuration
cp .env.example .env
# (edit .env if you need non-default ports/credentials)

# 3. Start infrastructure (PostgreSQL + Redis)
docker compose -f docker/docker-compose.yml --env-file .env up -d

# 4. Create and activate a Python virtual environment
py -m venv venv          # Windows
# python3 -m venv venv   # macOS/Linux

venv\Scripts\activate        # Windows (cmd/PowerShell)
# source venv/bin/activate   # macOS/Linux

# 5. Install dependencies
pip install -r requirements.txt

# 6. Verify the environment
pytest
```

If `pytest` passes with PostgreSQL and Redis tests **not skipped**, your environment is fully working. If they're skipped, Docker services aren't reachable yet — check `docker compose ps`.

**Expected setup time:** under 5 minutes on a machine that already has Docker Desktop and Python installed.

### Stopping services

```bash
docker compose -f docker/docker-compose.yml down       # stop containers, keep data
docker compose -f docker/docker-compose.yml down -v    # stop containers, wipe data volumes
```

### Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `docker compose` command not found | Docker Desktop not installed or not started | Install Docker Desktop, launch it, wait for the whale icon to stabilize |
| Postgres/Redis tests skipped | Containers not running or not healthy yet | `docker compose -f docker/docker-compose.yml ps` — wait for `healthy` status |
| Port already in use | Another service on 5432/6379 | Change `POSTGRES_PORT`/`REDIS_PORT` in `.env` |
| `pytest` can't import `src` | Run from project root, not a subdirectory | `cd` to project root before running `pytest` |

## Project Structure

```
├── docker/              # Docker Compose + container configuration
├── src/                 # Application source code
├── tests/               # Test suite
├── data/                # Local data directory (gitignored contents)
├── .env.example         # Environment variable template
└── requirements.txt     # Python dependencies
```

Full module breakdown (acquisition, processing, features, intelligence, etc.) is introduced in Checkpoint 0.3 per the roadmap.

## Contributing

Development follows [`IMPLEMENTATION_ROADMAP.md`](IMPLEMENTATION_ROADMAP.md) strictly: one checkpoint at a time, in order, with its Definition of Done verified before moving on.
