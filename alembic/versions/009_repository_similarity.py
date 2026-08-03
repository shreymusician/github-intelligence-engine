"""009_similarity

Creates repository_similarity: precomputed top-K "most similar
repositories" pairs, powering SYSTEM_ARCHITECTURE.md Module 7
(Recommendations) and IMPLEMENTATION_ROADMAP.md Checkpoints 3.3/5.1,
per DATABASE_DESIGN.md Sec2.12 and QUERY_DRIVEN_SCHEMA_DESIGN.md
Sec14.13.

Last migration in the sequence: the only self-referential table, and
per DATABASE_DESIGN.md Sec2.12 it is derived from embeddings and
metrics rather than an independent data source - logically the last
piece of the intelligence pipeline to materialize.

Top-K, not a full N x N matrix: at V1 scale a full matrix is trivial,
but SYSTEM_ARCHITECTURE.md Sec9 plans growth to 10K/100K+ repositories,
where an N^2 relationship table becomes untenable (~2.5B pairs past
~50K repos). Storing only the top-K per repository per method keeps
growth linear in repository count. DATABASE_ARCHITECT_REVIEW.md
validates this at 1M repositories, K=20, 2 methods -> 40M rows -
linear, not quadratic - "the single decision in the whole schema that
most clearly separates a design that survives this scale from one that
doesn't."

Multiple methods, independently queryable: embedding cosine distance,
engineered-feature distance, or a hybrid, per
TECHNICAL_IMPLEMENTATION_IDEAS.md Sec7's ranking-signal framework.
similarity_method = 'hybrid' has no weighting-breakdown column
(DATABASE_ARCHITECT_REVIEW.md notes this as under-specified but
explicitly not a current defect - no query needs it - so none is added
here).

Lifecycle: recomputed on each intelligence-generation cycle; old rows
for a method are replaced, not accumulated - unlike
repository_metrics/repository_scores, there is no historical-trend use
case identified for this table (DATABASE_DESIGN.md Sec2.12). This is
also why, unlike those two tables plus repository_snapshot and
repository_dependencies, repository_similarity has no declared
partition key in QUERY_DRIVEN_SCHEMA_DESIGN.md Sec16 - it does not grow
unbounded over time the way an append-only table does.

Schema dependency: only repositories.id (Migration 002), via two FKs
(repository_id, similar_repository_id) both pointing back to the same
table - the only self-referential table in the schema. Only a
soft/application-level dependency on 008_embeddings - not a schema
dependency, since similarity_method can also be 'feature_distance',
sourced from 006_feature_store instead.

Indexes: the composite PK covers "top-K for repository X, method Y"
point lookups; an explicit
INDEX(repository_id, similarity_method, rank) guarantees that ordering
is index-served, not sorted at query time, per
QUERY_DRIVEN_SCHEMA_DESIGN.md Sec14.13 and Sec15's Cross-Cutting Index
Summary.

Revision ID: 009
Revises: 008
Create Date: 2026-08-03

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '009'
down_revision = '008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    similarity_method = postgresql.ENUM(
        'embedding_cosine', 'feature_distance', 'hybrid',
        name='similarity_method',
    )

    op.create_table(
        'repository_similarity',
        sa.Column('repository_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('similar_repository_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('similarity_method', similarity_method, nullable=False),
        sa.Column('similarity_score', sa.Numeric(5, 4), nullable=False),
        sa.Column('rank', sa.SmallInteger(), nullable=False),
        sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(
            ['repository_id'], ['repositories.id'],
            name=op.f('fk_repository_similarity_repository_id_repositories'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['similar_repository_id'], ['repositories.id'],
            name=op.f('fk_repository_similarity_similar_repository_id_repositories'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint(
            'repository_id', 'similar_repository_id', 'similarity_method',
            name=op.f('pk_repository_similarity'),
        ),
        sa.CheckConstraint(
            'repository_id <> similar_repository_id',
            name=op.f('ck_repository_similarity_no_self_similarity'),
        ),
    )

    op.create_index(
        op.f('ix_repository_similarity_repository_id_similarity_method_rank'),
        'repository_similarity',
        ['repository_id', 'similarity_method', 'rank'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_repository_similarity_repository_id_similarity_method_rank'), table_name='repository_similarity')
    op.drop_table('repository_similarity')

    postgresql.ENUM(name='similarity_method').drop(op.get_bind(), checkfirst=True)
