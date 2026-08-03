"""008_embeddings

Creates repository_embeddings: model-scoped dense vector representations
of each repository (generated from README + technology stack + metadata),
enabling semantic search and similarity computation, per
DATABASE_DESIGN.md Sec2.11 and REPOSITORY_INTELLIGENCE_ENGINE.md Sec3.

Requires the vector extension (pgvector), assumed present per
MIGRATION_STRATEGY.md's 008_embeddings rationale - infrastructure vs.
schema separation (ADR-003). This migration does not install the
extension itself.

Deliberately separate from repository_scores (Migration 007): different
data shape and purpose entirely - repository_scores holds scalar
intelligence outputs; repository_embeddings holds dense vectors for
semantic similarity. First table with a genuinely different
storage/index type (pgvector ANN index), isolated so a future
embedding-model migration or index-tuning migration has an isolated
blast radius.

Model-scoped, not a single vector column: an embedding is meaningless
without knowing which model produced it - a cosine-similarity
comparison between vectors from two different models is not valid.
Every row is scoped to (repository_id, model_name); model_version is
tracked alongside for finer-grained versioning within a model family.
Lifecycle: recomputed when source text changes meaningfully or a new/
upgraded model is adopted; old model's rows are NOT deleted, enabling
comparison of search quality across model versions
(DATABASE_DESIGN.md Sec2.11).

Embedding dimension is vector(768), per DATABASE_ARCHITECT_REVIEW.md
Finding 7 (High severity): the original design typed this column
vector(1536) (OpenAI's text-embedding-ada-002 dimensionality) while the
project's actual V1 tooling (TECHNICAL_IMPLEMENTATION_IDEAS.md Sec5-6)
is Hugging Face Sentence Transformers, which does not produce
1536-dimensional output - a genuine defect that would have broken on
first use. 768 dimensions matches a standard Sentence Transformers
model (e.g. all-mpnet-base-v2). Supporting a second, differently-
dimensioned model later is explicitly out of V1 scope (would require a
new table or column-width migration) - a deliberate YAGNI call, not a
gap. Not changed here.

PK: composite (repository_id, model_name), per
QUERY_DRIVEN_SCHEMA_DESIGN.md Sec14.12. Both PK columns are implicitly
NOT NULL by SQL PK semantics, same treatment as repository_technologies'
composite PK in Migration 003.

No ANN index (HNSW/IVFFlat) is created in this migration - Sec14.12
explicitly defers the specific index type and parameters to a
Milestone 4.2 implementation decision ("not fixed at schema-design
time, since it depends on eventual corpus size"). Only the implicit
btree backing the composite PK exists here.

Schema dependency: only repositories.id (Migration 002) - no
relationship to Migrations 001, 003, 004, 005, 006, or 007. Alembic
chain position 007->008 is linear-ordering only. 009_similarity has
only a soft/application-level dependency on this migration (no DB-level
FK) - similarity_method can be 'embedding_cosine' (sourced from this
table) or 'feature_distance' (sourced from 006 instead).

Revision ID: 008
Revises: 007
Create Date: 2026-08-01

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision = '008'
down_revision = '007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'repository_embeddings',
        sa.Column('repository_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('model_name', sa.Text(), nullable=False),
        sa.Column('model_version', sa.Text(), nullable=False),
        sa.Column('embedding', Vector(768), nullable=False),
        sa.Column('source_text_hash', sa.Text(), nullable=False),
        sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(
            ['repository_id'], ['repositories.id'],
            name=op.f('fk_repository_embeddings_repository_id_repositories'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('repository_id', 'model_name', name=op.f('pk_repository_embeddings')),
    )


def downgrade() -> None:
    op.drop_table('repository_embeddings')
