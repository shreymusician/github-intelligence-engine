-- PostgreSQL Initialization Script
--
-- This script runs automatically when PostgreSQL container starts
-- (only on first startup, not on subsequent restarts)
--
-- Responsibilities:
-- 1. Create application user (separate from admin)
-- 2. Create application database with proper settings
-- 3. Grant permissions appropriately
-- 4. Install required extensions
--
-- Security Principle:
-- Application user has minimal required permissions
-- (can't drop databases, modify system settings)

-- Create application user (non-superuser)
-- Password set from environment or use default
CREATE USER repo_user WITH PASSWORD 'repo_password';

-- Create application database
-- Using UTF-8 encoding (required for full-text search, international characters)
CREATE DATABASE ai_analytics
    ENCODING 'UTF8'
    LC_COLLATE 'en_US.UTF-8'
    LC_CTYPE 'en_US.UTF-8'
    OWNER repo_user;

-- Connect to application database for further setup
\c ai_analytics

-- Install pgvector extension (for embeddings/vector similarity search)
-- Required for: Repository embeddings, semantic similarity search
-- Dependency: pgvector must be installed in PostgreSQL
CREATE EXTENSION IF NOT EXISTS vector;

-- Install uuid-ossp extension (for generating UUIDs)
-- Required for: Primary keys that are globally unique and safe to move
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Install pg_trgm extension (trigram text search)
-- Required for: Better full-text search, fuzzy matching
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Install unaccent extension (remove accents for search)
-- Required for: Searching "cafe" finds "café"
CREATE EXTENSION IF NOT EXISTS unaccent;

-- Grant schema permissions to application user
GRANT CREATE ON SCHEMA public TO repo_user;
GRANT USAGE ON SCHEMA public TO repo_user;

-- Grant permissions on future objects (tables, indexes, sequences, etc.)
-- This ensures app user can use any tables created in public schema
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO repo_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO repo_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO repo_user;

-- Allow app user to create temporary tables (for queries)
GRANT TEMP ON DATABASE ai_analytics TO repo_user;

-- Create basic schema structure (initial tables)
--
-- repositories: Core table storing repository metadata
CREATE TABLE IF NOT EXISTS repositories (
    -- Unique identifier
    id BIGSERIAL PRIMARY KEY,

    -- GitHub identifiers
    github_id BIGINT UNIQUE NOT NULL,
    owner VARCHAR(255) NOT NULL,
    name VARCHAR(255) NOT NULL,

    -- URLs
    url VARCHAR(1024) NOT NULL,

    -- Metadata
    description TEXT,
    language VARCHAR(50),
    license VARCHAR(100),

    -- Statistics
    stars INTEGER DEFAULT 0,
    forks INTEGER DEFAULT 0,
    watchers INTEGER DEFAULT 0,
    open_issues INTEGER DEFAULT 0,

    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_commit_at TIMESTAMP,

    -- Search optimization
    full_text_search TSVECTOR GENERATED ALWAYS AS (
        setweight(to_tsvector('english', COALESCE(name, '')), 'A') ||
        setweight(to_tsvector('english', COALESCE(description, '')), 'B')
    ) STORED
);

-- Index for performance
CREATE INDEX idx_repositories_owner ON repositories(owner);
CREATE INDEX idx_repositories_language ON repositories(language);
CREATE INDEX idx_repositories_stars ON repositories(stars DESC);
CREATE INDEX idx_repositories_fts ON repositories USING GIN(full_text_search);
CREATE INDEX idx_repositories_updated_at ON repositories(updated_at DESC);

-- Grant permissions to app user
GRANT SELECT, INSERT, UPDATE, DELETE ON repositories TO repo_user;
GRANT USAGE, SELECT ON repositories_id_seq TO repo_user;

-- technologies: Reference table for technologies (languages, frameworks, databases)
CREATE TABLE IF NOT EXISTS technologies (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) UNIQUE NOT NULL,
    category VARCHAR(50) NOT NULL, -- 'language', 'framework', 'database', 'tool', 'platform'
    description TEXT
);

-- Index for lookups
CREATE INDEX idx_technologies_name ON technologies(name);
CREATE INDEX idx_technologies_category ON technologies(category);

-- Grant permissions
GRANT SELECT, INSERT, UPDATE ON technologies TO repo_user;
GRANT USAGE, SELECT ON technologies_id_seq TO repo_user;

-- repository_technologies: Join table (many-to-many)
CREATE TABLE IF NOT EXISTS repository_technologies (
    repository_id BIGINT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    technology_id INT NOT NULL REFERENCES technologies(id) ON DELETE CASCADE,
    is_primary BOOLEAN DEFAULT FALSE, -- Is this the primary technology?
    PRIMARY KEY (repository_id, technology_id)
);

-- Index for queries
CREATE INDEX idx_repo_tech_technology ON repository_technologies(technology_id);
CREATE INDEX idx_repo_tech_is_primary ON repository_technologies(is_primary);

-- Grant permissions
GRANT SELECT, INSERT, UPDATE, DELETE ON repository_technologies TO repo_user;

-- Log message indicating initialization complete
-- (visible in Docker logs)
\echo ''
\echo '============================================================'
\echo 'PostgreSQL Initialization Complete'
\echo '============================================================'
\echo 'Database: ai_analytics'
\echo 'Application User: repo_user'
\echo 'Extensions Installed: vector, uuid-ossp, pg_trgm, unaccent'
\echo 'Initial Schema: repositories, technologies, repository_technologies'
\echo '============================================================'
\echo ''
