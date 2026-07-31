# ADR-002: Use PostgreSQL as Primary Database

**Status:** Accepted  
**Date:** 2026-07-31  
**Checkpoint:** 0.1  

## Context

We need a database that handles complex queries, large datasets, and advanced features (embeddings, full-text search).

**Options considered:**
1. **PostgreSQL** (chosen) — Mature, rich feature set, proven at scale
2. MongoDB — Document-oriented, flexible schema
3. MySQL — Simpler, widely used
4. Specialized databases — Elasticsearch, Redis only

## Decision

Use PostgreSQL 17 (Alpine) as the primary operational database.

Include extensions:
- `pgvector` — Vector similarity search (embeddings)
- `uuid-ossp` — UUID generation
- `pg_trgm` — Trigram text search
- `unaccent` — Accent-insensitive search

## Rationale

**ACID Transactions:** Data integrity guaranteed. Critical for financial and transactional data.

**Complex Queries:** JOINs, aggregations, window functions, recursive CTEs. Our schema is normalized.

**Advanced Features:**
- pgvector: Required for semantic search (embeddings)
- Full-text search: Built-in, no external search engine needed initially
- JSON/JSONB: Store semi-structured data without extra tools

**Proven at Scale:** Used by Google, Amazon, Stripe, GitHub. Scales to billions of rows.

**Ecosystem:** Excellent tools (pgAdmin, DBeaver), libraries, documentation.

**Cost:** Open source, free at any scale.

## Consequences

**Positive:**
- ✅ Can handle all milestone requirements through V4+
- ✅ Migration to managed services (RDS, Cloud SQL) straightforward
- ✅ Native support for vectors (pgvector), search (FTS)
- ✅ Battle-tested in production environments

**Negative:**
- ⚠️ More complex than simple key-value stores
- ⚠️ Requires schema design (good thing, actually)
- ⚠️ Setup takes ~5s to start (negligible for dev)

## Alternatives Rejected

**MongoDB:**
- Pro: Flexible schema, JSON-native
- Con: No ACID transactions (recent versions have limited support), no native vector search, not suitable for our structured data

**MySQL:**
- Pro: Simpler, widely known
- Con: Less advanced features, no pgvector, weaker full-text search

**Elasticsearch-only:**
- Pro: Excellent search
- Con: Not a primary database, would need separate persistence layer, overkill for MVP

## Migration Path

If PostgreSQL becomes insufficient (unlikely before Year 2):
1. Add read replicas for scaling reads
2. Add ClickHouse for analytics (time-series, large aggregations)
3. Potentially shard by language/industry if needed

Each can be added without rewriting application code.

## When to Revisit

- If query performance becomes unacceptable (unlikely before 10M+ rows)
- If advanced vector operations need specialized vector DB
- If cost becomes a factor (unlikely for open source project)

## Related Decisions

- ADR-001: Use Docker Compose
- ADR-003: Separate infrastructure from schema

## Future Additions

Redis caching layer (separate decision):
- PostgreSQL: Complex queries, ACID, source of truth
- Redis: Query result caching, sessions, leaderboards
- Elasticsearch: (Checkpoint 4.2+) Full-text search at scale

Both are complementary, not replacements.
