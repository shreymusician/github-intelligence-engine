-- PostgreSQL Infrastructure Initialization Script
--
-- RESPONSIBILITY: Infrastructure initialization ONLY
-- - Install required extensions
-- - Create database roles/users
-- - Configure permissions
--
-- NOT RESPONSIBLE FOR:
-- - Application schema (tables, indexes, constraints)
-- - Data model evolution
--
-- RATIONALE:
-- Infrastructure (database server, extensions) is initialized once.
-- Application schema evolves via Alembic migrations (see Checkpoint 0.2).
-- This separation enables proper schema versioning and rollback.
--
-- PHILOSOPHY:
-- - One responsibility: Infrastructure setup
-- - Schema is managed by Alembic migrations
-- - No hardcoded application tables here
-- - Extensions pre-installed for future use

-- ============================================================================
-- SECURITY: Create application user (non-superuser)
-- ============================================================================
-- The application user has minimal required permissions:
-- - Can read/write schema in public
-- - Cannot drop databases or modify system settings
-- - Follows principle of least privilege

CREATE USER repo_user WITH PASSWORD 'repo_password';

-- ============================================================================
-- CREATE APPLICATION DATABASE
-- ============================================================================
-- Portability note: LC_COLLATE/LC_CTYPE are deliberately NOT specified here.
--
-- CREATE DATABASE requires its collation to match the template database's
-- collation EXACTLY (byte-for-byte), not just "equivalently". The base image
-- is postgres:17-alpine (musl libc), whose initdb reports the container
-- locale as "en_US.utf8" (lowercase, no hyphen) — not the glibc-style
-- "en_US.UTF-8" (uppercase, hyphenated) this script previously hardcoded.
-- That string mismatch, not a real locale incompatibility, is what
-- PostgreSQL's "new collation is incompatible with the collation of the
-- template database" error reports.
--
-- Hardcoding the "correct" Alpine string would just trade one fragile
-- assumption for another (and would break again on a glibc-based image).
-- Omitting LC_COLLATE/LC_CTYPE lets CREATE DATABASE inherit them from
-- template1, which was already initialized correctly for whatever image
-- this runs on — the portable default, per project instruction.
--
-- ENCODING 'UTF8' is kept explicit: unlike collation, encoding has no
-- exact-string-match requirement against the template (only a compatibility
-- check), postgres:17-alpine's template1 is UTF8 by default, and asserting
-- it here documents the requirement (FTS, international text) without
-- reintroducing a portability risk.

CREATE DATABASE ai_analytics
    ENCODING 'UTF8'
    OWNER repo_user;

-- Switch to application database for further configuration
\c ai_analytics

-- ============================================================================
-- INSTALL EXTENSIONS
-- ============================================================================
-- These extensions are pre-installed for application use.
-- They don't create schema tables; they provide functionality.

-- pgvector: Vector similarity search (for embeddings)
-- Used by: Semantic search (Checkpoint 4.2+)
CREATE EXTENSION IF NOT EXISTS vector;

-- uuid-ossp: UUID generation functions
-- Used by: Primary keys that are globally unique
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- pg_trgm: Trigram text search
-- Used by: Fuzzy text matching, partial word search
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- unaccent: Remove accents from text
-- Used by: Search "cafe" and find "café"
CREATE EXTENSION IF NOT EXISTS unaccent;

-- ============================================================================
-- CONFIGURE PERMISSIONS
-- ============================================================================
-- Grant permissions on schema (not specific tables—those come from Alembic)

-- Allow app user to create objects in public schema
GRANT CREATE ON SCHEMA public TO repo_user;
GRANT USAGE ON SCHEMA public TO repo_user;

-- Set default permissions for future objects
-- When Alembic creates tables, repo_user automatically has these permissions
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO repo_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO repo_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO repo_user;

-- Allow app user to create temporary tables (for complex queries)
GRANT TEMP ON DATABASE ai_analytics TO repo_user;

-- ============================================================================
-- INITIALIZATION COMPLETE
-- ============================================================================
-- Database is ready for Alembic migrations (Checkpoint 0.2+)
-- No schema tables are created here—schema is managed by Alembic

\echo ''
\echo '========================================================================='
\echo 'PostgreSQL Infrastructure Initialization Complete'
\echo '========================================================================='
\echo 'Database: ai_analytics'
\echo 'Admin User: postgres'
\echo 'Application User: repo_user'
\echo ''
\echo 'Extensions Installed:'
\echo '  - pgvector (semantic search)'
\echo '  - uuid-ossp (UUID generation)'
\echo '  - pg_trgm (trigram search)'
\echo '  - unaccent (accent removal)'
\echo ''
\echo 'Schema Status:'
\echo '  - No application tables created (infrastructure only)'
\echo '  - Schema is managed by Alembic migrations (Checkpoint 0.2+)'
\echo '  - To create schema: run alembic upgrade head'
\echo ''
\echo 'Ready for Alembic migrations.'
\echo '========================================================================='
\echo ''
