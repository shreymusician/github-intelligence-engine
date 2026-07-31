# Project Folder Structure

Repository Intelligence Engine - Development Folder Organization

## Overview

This project follows a **modular monolith** architecture, organized by responsibility layer. Each folder is independent but communicates through well-defined interfaces.

**Key Principle:** Data flows top-to-bottom; presentation is independent of computation.

---

## Root Level

```
repository-intelligence-engine/
├── README.md                  # Project overview and quick start
├── FOLDER_STRUCTURE.md        # This file
├── CLAUDE.md                  # Product vision (frozen)
├── SYSTEM_ARCHITECTURE.md     # System design blueprint
├── IMPLEMENTATION_ROADMAP.md  # Development roadmap with checkpoints
├── docker-compose.yml         # Local development infrastructure
├── .env.example              # Environment variables documentation
├── .gitignore               # Git ignore rules
├── .dockerignore             # Docker build ignore rules
└── ...
```

---

## `/src` - Application Source Code

**Responsibility:** Core application logic organized by data flow and functionality.

**Why This Organization?**
- Follows intelligence pipeline: acquisition → processing → features → intelligence → application
- Each module has clear input/output contracts
- Easy to parallelize work (different developers on different modules)
- Can move to microservices later (module → service migration path clear)

```
src/
├── __init__.py              # Package initialization
├── config.py                # Configuration management, env loading
├── logging.py               # Structured logging setup
│
├── acquisition/             # DATA COLLECTION MODULE
│   ├── __init__.py
│   ├── README.md           # Module documentation
│   ├── github_api.py        # GitHub REST API client
│   ├── github_archive.py    # GitHub BigQuery Archive queries
│   ├── repository_fetcher.py # Repository content downloader
│   ├── rate_limiter.py      # GitHub API rate limit manager
│   └── errors.py            # Acquisition-specific exceptions
│
├── processing/              # DATA PROCESSING & VALIDATION MODULE
│   ├── __init__.py
│   ├── README.md
│   ├── validation.py        # Schema validation, type checking
│   ├── normalization.py     # Data standardization, cleaning
│   ├── deduplication.py     # Duplicate detection and removal
│   ├── quality_check.py     # Data quality assessment
│   └── errors.py
│
├── features/                # FEATURE ENGINEERING MODULE
│   ├── __init__.py
│   ├── README.md
│   ├── activity_features.py # Commits, contributors, activity metrics
│   ├── content_features.py  # Documentation quality, README analysis
│   ├── technology_features.py # Tech stack detection, extraction
│   ├── dependency_features.py # Dependency analysis, health scoring
│   ├── complexity_features.py # Code complexity sampling
│   ├── embeddings.py        # Embedding generation using models
│   └── errors.py
│
├── intelligence/            # INTELLIGENCE GENERATION MODULE
│   ├── __init__.py
│   ├── README.md
│   ├── scoring.py           # Difficulty and quality scoring
│   ├── similarity.py        # Repository similarity computation
│   ├── analysis.py          # Statistical analysis, trend detection
│   ├── models.py            # ML model definitions and inference
│   ├── validators.py        # Result validation, sanity checks
│   └── errors.py
│
├── search/                  # SEARCH & DISCOVERY MODULE
│   ├── __init__.py
│   ├── README.md
│   ├── keyword_search.py    # Full-text search implementation
│   ├── semantic_search.py   # Vector similarity search
│   ├── ranking.py           # Result ranking and combination
│   ├── filters.py           # Query filtering logic
│   └── errors.py
│
├── recommendations/         # RECOMMENDATION ENGINE MODULE
│   ├── __init__.py
│   ├── README.md
│   ├── content_based.py     # Content-based recommendations
│   ├── collaborative.py     # Collaborative filtering (future)
│   ├── graph_based.py       # Knowledge graph recommendations
│   ├── ranking.py           # Recommendation ranking
│   ├── diversity.py         # Diversity and novelty optimization
│   └── errors.py
│
├── analytics/               # ANALYTICS & INSIGHTS MODULE
│   ├── __init__.py
│   ├── README.md
│   ├── statistics.py        # Statistical computations
│   ├── trends.py            # Trend detection and analysis
│   ├── reports.py           # Report generation
│   └── errors.py
│
├── database/                # DATABASE ACCESS LAYER
│   ├── __init__.py
│   ├── README.md
│   ├── connection.py        # Database connection pool management
│   ├── models.py            # ORM models or raw query builders
│   ├── migrations.py        # Database schema migrations
│   ├── queries.py           # Common queries and access patterns
│   └── errors.py
│
├── cache/                   # CACHING LAYER
│   ├── __init__.py
│   ├── README.md
│   ├── redis_client.py      # Redis connection and client
│   ├── keys.py              # Cache key naming conventions
│   ├── decorators.py        # @cache decorators
│   └── strategies.py        # Cache invalidation strategies
│
├── api/                     # API LAYER (later, when needed)
│   ├── __init__.py
│   ├── server.py            # FastAPI/Flask application
│   ├── routes.py            # API endpoint definitions
│   ├── schemas.py           # Request/response schemas
│   ├── middleware.py        # Authentication, logging, CORS
│   └── errors.py
│
├── common/                  # SHARED UTILITIES
│   ├── __init__.py
│   ├── README.md
│   ├── constants.py         # Application-wide constants
│   ├── decorators.py        # Common decorators
│   ├── exceptions.py        # Base exception classes
│   ├── typing.py            # Type aliases and protocols
│   ├── utils.py             # Utility functions
│   └── metrics.py           # Metrics and instrumentation
│
└── main.py                  # Application entry point
```

**Module Communication Pattern:**
```
Input Data
    ↓
[acquisition] → Raw Data
    ↓
[processing] → Validated Data
    ↓
[features] → Feature Vectors
    ↓
[intelligence] → Scores & Insights
    ↓
[search, recommendations, analytics] → User-Facing Services
    ↓
[api] → HTTP Responses
```

---

## `/tests` - Test Suite

**Responsibility:** Ensure code quality and correctness through comprehensive testing.

**Philosophy:**
- Tests are documentation of how to use code
- Test first (or at least early) thinking
- Clear test names explain intent
- Unit tests ≥ 80% coverage target

```
tests/
├── __init__.py
├── conftest.py              # pytest fixtures and configuration
│
├── unit/                    # Unit tests (isolated component testing)
│   ├── __init__.py
│   ├── test_acquisition/    # Test each acquisition module
│   │   ├── test_github_api.py
│   │   └── test_repository_fetcher.py
│   ├── test_processing/
│   ├── test_features/
│   ├── test_intelligence/
│   ├── test_search/
│   ├── test_recommendations/
│   └── ...
│
├── integration/             # Integration tests (modules working together)
│   ├── __init__.py
│   ├── test_acquisition_to_processing.py
│   ├── test_full_pipeline.py
│   ├── test_search_with_database.py
│   └── ...
│
├── fixtures/                # Test data and database fixtures
│   ├── __init__.py
│   ├── sample_repositories.py
│   ├── sample_technologies.py
│   └── ...
│
└── e2e/                     # End-to-end tests (if API exists)
    ├── __init__.py
    └── test_api_flows.py
```

---

## `/data` - Local Data Storage

**Responsibility:** Store data artifacts during development and testing.

**Important:** This directory is .gitignored (never commit data files).

```
data/
├── raw/                     # Raw data as fetched from GitHub
│   └── .gitkeep
│
├── processed/               # Cleaned and validated data
│   └── .gitkeep
│
├── features/                # Computed feature vectors
│   └── .gitkeep
│
├── models/                  # Trained ML models (pickled)
│   └── .gitkeep
│
├── cache/                   # Local cache for expensive computations
│   └── .gitkeep
│
└── README.md               # Documentation of data directory
```

---

## `/scripts` - Utility Scripts and Database

**Responsibility:** One-off scripts, database initialization, and operational tasks.

```
scripts/
├── init-postgres.sql        # PostgreSQL initialization (creates schema)
├── init-redis.conf          # Redis configuration (if needed)
├── pgadmin-servers.json     # pgAdmin server configuration
│
├── seed_data.py             # Populate database with test data
├── reset_database.py        # Clear all data (development only)
├── run_pipeline.py          # Execute full acquisition→processing pipeline
├── analyze_data.py          # One-off analysis scripts
│
└── README.md               # Scripts documentation
```

---

## `/notebooks` - Exploratory Analysis

**Responsibility:** Research, experimentation, and data visualization.

**Philosophy:**
- Jupyter notebooks for exploratory work
- NOT part of production pipeline
- Each notebook documents one investigation/analysis
- Results should be moved to code when stable

```
notebooks/
├── 01_data_exploration.ipynb       # Initial data overview
├── 02_feature_analysis.ipynb       # Feature distribution analysis
├── 03_similarity_validation.ipynb  # Validate similarity computations
├── 04_model_evaluation.ipynb       # ML model performance
│
└── README.md                       # Notebook guidelines
```

---

## `/docs` - Documentation

**Responsibility:** User-facing and developer documentation.

```
docs/
├── README.md                # Documentation index
├── ARCHITECTURE.md          # Architecture decisions
├── API.md                   # API endpoint documentation (future)
├── DEPLOYMENT.md            # Deployment instructions
│
├── guides/                  # How-to guides
│   ├── setup.md            # Local development setup
│   ├── contributing.md     # Contribution guidelines
│   └── troubleshooting.md  # Common issues and solutions
│
└── research/               # Research notes and findings
    ├── feature_analysis.md
    ├── quality_scoring.md
    └── recommendation_analysis.md
```

---

## `/docker` - Docker Configuration

**Responsibility:** Container definitions and deployment configuration.

```
docker/
├── Dockerfile              # Main application Dockerfile
├── Dockerfile.dev          # Development Dockerfile (with debug tools)
├── .dockerignore          # What NOT to include in image
│
└── entrypoint.sh          # Container startup script
```

---

## `/config` - Configuration Files

**Responsibility:** Configuration for different environments.

```
config/
├── settings.py            # Global settings (loaded from env)
├── database.yaml          # Database connection configuration
├── features.yaml          # Feature engineering configuration
├── models.yaml            # ML model configuration
│
└── README.md             # Configuration documentation
```

---

## Root Configuration Files

```
.env.example              # Environment variables template (committed)
.env                      # Actual environment variables (git-ignored, don't commit)
.gitignore               # Git ignore rules
.dockerignore            # Docker ignore rules
docker-compose.yml       # Local development container orchestration
requirements.txt         # Python package dependencies
README.md                # Project overview
```

---

## Directory Dependency Diagram

```
Lower layers depend on upper layers (data flows down):

┌─ presentation (notebooks, docs)
│
├─ acquisition (GitHub APIs)
├─ processing (validation, cleaning)
├─ features (compute metrics)
├─ intelligence (generate scores)
├─ search (find repositories)
├─ recommendations (suggest repositories)
├─ analytics (statistics, reports)
│
├─ database (shared storage)
├─ cache (shared performance)
├─ config (shared settings)
│
└─ common (utilities, constants)

Key: No circular dependencies
     Lower levels don't import from higher levels
     Configuration flows from root
```

---

## File Naming Conventions

**Python Files:**
- `module_name.py` (lowercase, underscores) - e.g., `similarity.py`
- `test_module_name.py` - e.g., `test_similarity.py`
- `errors.py` or `exceptions.py` - error classes

**Class Names:**
- `CamelCase` - e.g., `RepositorySimilarity`
- `TestCamelCase` for tests - e.g., `TestRepositorySimilarity`

**Database:**
- Table names: `snake_case` - e.g., `repositories`
- Columns: `snake_case` - e.g., `created_at`
- Indexes: `idx_table_column` - e.g., `idx_repositories_language`

---

## Key Principles

1. **Separation of Concerns**
   - Each module has ONE reason to change
   - Clear inputs and outputs
   - No module knows about modules above it

2. **Data Flow**
   - Data moves in one direction: acquisition → API
   - No backward references

3. **Testability**
   - Each module can be tested in isolation
   - Fixtures provide sample data
   - Mock external dependencies

4. **Scalability**
   - Modular structure can migrate to microservices
   - Each module can have its own database or scaling strategy
   - No shared state (except through database/cache)

5. **Maintainability**
   - README in each module explains purpose
   - Clear function/class documentation
   - No hidden dependencies

---

## Adding New Features

When adding a new feature, follow this process:

1. **Identify which module owns it** (use the responsibility list above)
2. **Create a new file** in that module (or add to existing)
3. **Write tests first** in `/tests/unit/test_module/`
4. **Document with README** update in the module
5. **Update this file** if changing structure

---

## Performance Notes

- **Database queries:** Indexed for common access patterns
- **Caching:** Redis for hot data (query results, recommendations)
- **Feature computation:** Batched for efficiency
- **Search:** PostgreSQL FTS initially, migrate to Elasticsearch if needed

