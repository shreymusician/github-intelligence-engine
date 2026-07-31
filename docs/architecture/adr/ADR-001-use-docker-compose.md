# ADR-001: Use Docker Compose for Local Development Environment

**Status:** Accepted  
**Date:** 2026-07-31  
**Checkpoint:** 0.1  

## Context

We need a reproducible local development environment that multiple developers can use immediately without setup friction.

**Options considered:**
1. **Docker Compose** (chosen) — Containerized services, one command to start
2. Local installations — Developers install PostgreSQL, Redis locally
3. Cloud development environment — Remote setup, always available
4. Hybrid — Some local, some remote

## Decision

Use Docker Compose for local development.

Define all services in `docker-compose.yml`:
- PostgreSQL 17 (primary database)
- Redis 7 (caching layer)
- pgAdmin 4 (database UI)

## Rationale

**Speed:** New developer onboarding: 5 minutes (download image once, then `docker compose up -d`)

**Reproducibility:** Everyone runs identical versions. Eliminates "works on my machine" problems.

**Isolation:** Services run in containers, don't interfere with host system or other projects.

**Production Parity:** Same container definitions can be used for production (via Kubernetes, etc.)

**Industry Standard:** Stripe, Netflix, Amazon use Docker Compose for local dev.

## Consequences

**Positive:**
- ✅ Consistent environment across team
- ✅ Easy to reset/clean (delete volumes)
- ✅ Scalable to production
- ✅ Every developer gets same tooling

**Negative:**
- ⚠️ Requires Docker Desktop installation
- ⚠️ Additional disk/memory usage
- ⚠️ Slightly slower than native processes

## Alternatives Rejected

**Local Installation:**
- Pro: No Docker overhead
- Con: Version mismatches between developers, setup friction, harder to scale

**Cloud Development Environment:**
- Pro: Always available, scalable
- Con: Requires network, can't work offline, more expensive

## When to Revisit

- If Docker becomes unavailable or problematic at scale
- If developer onboarding becomes a bottleneck
- If cloud native development becomes standard

## Related Decisions

- ADR-002: Use PostgreSQL (database technology choice)
- Checkpoint 0.1: Development Environment Setup
