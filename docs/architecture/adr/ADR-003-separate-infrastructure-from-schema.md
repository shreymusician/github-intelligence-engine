# ADR-003: Separate Infrastructure Initialization from Schema Evolution

**Status:** Accepted  
**Date:** 2026-07-31  
**Checkpoint:** 0.1  

## Context

We need clear separation between:
- **Infrastructure:** PostgreSQL server, extensions, users (one-time setup)
- **Schema:** Tables, indexes, constraints (evolves over time)
- **Application Logic:** ORM models, queries, business logic

Initial approach of combining these caused tight coupling and violated separation of concerns.

## Decision

Three distinct layers with clear responsibilities:

```
┌─────────────────────────────────────────┐
│  init-postgres.sql  (Checkpoint 0.1)    │
│  ───────────────────────────────────    │
│  Responsibility: Infrastructure only    │
│  - Create database                      │
│  - Create users/roles                   │
│  - Install extensions                   │
│  - Configure permissions                │
│  ✗ No application tables                │
└─────────────────────────────────────────┘
               ↓
┌─────────────────────────────────────────┐
│  Alembic Migrations (Checkpoint 0.2+)   │
│  ───────────────────────────────────    │
│  Responsibility: Schema evolution       │
│  - Create/modify/drop tables            │
│  - Add/remove columns                   │
│  - Create indexes                       │
│  - Manage constraints                   │
│  ✓ Single source of truth for schema    │
│  ✓ Versionable and reversible           │
└─────────────────────────────────────────┘
               ↓
┌─────────────────────────────────────────┐
│  SQLAlchemy ORM Models (Checkpoint 0.3) │
│  ───────────────────────────────────    │
│  Responsibility: Application logic      │
│  - Query builders                       │
│  - Business logic                       │
│  - Application-specific methods         │
└─────────────────────────────────────────┘
```

## Rationale

**Single Responsibility Principle:** Each layer has one reason to change:
- Infrastructure: Rarely changes (version bumps)
- Schema: Changes with data model evolution
- Application: Changes with business logic

**Testability:** Each layer independently testable:
- Infrastructure: "Database starts and is ready"
- Schema: "Migrations apply/rollback cleanly"
- Application: "Queries work correctly"

**Versionability:** Alembic provides:
- Git history of schema changes
- Rollback capability (critical for production)
- Automated migration generation
- Collaboration: Multiple developers can add migrations

**Production Safety:**
- No ad-hoc schema changes
- Every change is reviewable and tested
- Migration logs provide audit trail
- Consistent across staging/production

**Future Extensibility:**
- Can migrate to managed database service without touching code
- Can add analytics database (ClickHouse) without schema changes
- Can extract Alembic into separate package if needed

## Consequences

**Positive:**
- ✅ Schema is properly versioned (git history)
- ✅ Can rollback production bugs (run previous migration)
- ✅ Multiple developers can work on schema simultaneously
- ✅ Clear audit trail of all schema changes
- ✅ Enables confident refactoring

**Negative:**
- ⚠️ Requires running migrations after pulling code (`alembic upgrade head`)
- ⚠️ Developer must understand Alembic basics
- ⚠️ No automatic schema detection (must write migrations)

## Trade-offs Made

**Option A (Chosen): Alembic + Manual Migrations**
- Pro: Explicit control, code review, audit trail
- Con: Developer discipline required

**Option B: Automatic Migration Generation (SQLAlchemy-migrate)**
- Pro: Less boilerplate
- Con: Can generate incorrect migrations, harder to review

**Option C: No migrations (init script only)**
- Pro: Simple initially
- Con: Can't evolve schema, production nightmare

**Verdict:** Option A is only suitable for production systems.

## Checkpoint Timeline

**Checkpoint 0.1:** Infrastructure initialization
- Database creation
- Extensions installed
- Users configured
- NO schema tables

**Checkpoint 0.2:** Schema design and first migration
- Alembic initialized
- Initial schema designed (repositories, technologies, etc.)
- First migration created: `001_initial_schema.py`
- Migration tested

**Checkpoint 0.3+:** Schema evolution
- New features add new migrations
- Migrations peer-reviewed
- All changes tracked in git

## When to Revisit

- If Alembic becomes a bottleneck (unlikely)
- If we need to support multiple database backends simultaneously
- If we move to a different ORM (unlikely)

## Related Decisions

- ADR-001: Use Docker Compose
- ADR-002: Use PostgreSQL
- Checkpoint 0.1: Development Environment Setup
- Checkpoint 0.2: Database Schema & Initialization

## Implementation Details

See:
- `init-postgres.sql` — Infrastructure initialization (Checkpoint 0.1)
- `alembic/` — Migration system (Checkpoint 0.2+)
- `requirements.txt` — SQLAlchemy + Alembic dependencies

## Migration Workflow

```bash
# After Checkpoint 0.2, typical workflow:

# Pull new code
git pull origin main

# Create migration for new schema
alembic revision --autogenerate -m "add users table"

# Review migration (human step)
vim alembic/versions/001_add_users_table.py

# Apply migration
alembic upgrade head

# Code uses new schema via ORM
python -c "from app.models import User; print(User.__tablename__)"
```

This is the industry standard for Python applications.
