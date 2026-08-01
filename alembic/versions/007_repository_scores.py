"""007_intelligence_scores

Creates repository_scores: versioned intelligence outputs (difficulty
score, quality score, community-health classification) per
DATABASE_DESIGN.md Sec2.10 and SYSTEM_ARCHITECTURE.md Module 4.

Deliberately separate from repository_metrics (Migration 006):
repository_metrics holds inputs (computed features); repository_scores
holds outputs (scores derived from those features by scoring algorithms).
There is no database-level FK from repository_scores to
repository_metrics - scores reference repositories directly. The
relationship exists only at the application layer (a scoring algorithm
reads metrics to produce a score row), per MIGRATION_STRATEGY.md's
007_intelligence_scores rationale.

Own migration because of a distinct owning module (Intelligence
Generation, Milestone 3) and distinct consumer pattern (read by Search
and Recommendations, not Feature Engineering).

Append-only and versioned, like repository_metrics: scoring algorithms
evolve, so a single mutable column would silently rewrite history and
make it impossible to distinguish "the repository improved" from "we
changed the formula." Every row is stamped with algorithm_version. A
"current" score is simply the most recent row for a given
(repository_id, score_type) - never updated in place, never deleted
except via CASCADE (DATABASE_DESIGN.md Sec2.10).

Schema dependency: only repositories.id (Migration 002) - no
relationship to Migrations 001, 003, 004, 005, or 006. Alembic chain
position 006->007 is linear-ordering only. Only a soft/application-level
dependency on 006_feature_store (scoring algorithms use metrics as
input), not a schema dependency.

No UNIQUE constraint on (repository_id, score_type) - deliberately,
since append-only versioning requires multiple rows per
(repository_id, score_type) over time (each new algorithm_version or
recomputation adds a row, it doesn't overwrite). This differs from
repository_snapshot/repository_metrics (Migrations 005/006), which both
have UNIQUE(repository_id, snapshot_date) since only one snapshot per
day is valid there.

No CHECK constraint on score_value - confirmed absent from
QUERY_DRIVEN_SCHEMA_DESIGN.md Sec14.11 and all other frozen documents
(unlike repository_metrics.top_contributor_commit_share's documented
0-1 CHECK). Not invented here.

Declared (not implemented) future partition key per
DATABASE_ARCHITECT_REVIEW.md Finding 9: computed_at (RANGE, monthly or
quarterly), same coordinated-partitioning posture as repository_metrics
(006) and repository_snapshot (005). No CREATE TABLE ... PARTITION BY
is created here.

Revision ID: 007
Revises: 006
Create Date: 2026-08-01

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '007'
down_revision = '006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    score_type = postgresql.ENUM(
        'difficulty', 'quality', 'community_health',
        name='score_type',
    )

    op.create_table(
        'repository_scores',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column('repository_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('score_type', score_type, nullable=False),
        sa.Column('score_value', sa.Numeric(5, 2), nullable=False),
        sa.Column('score_breakdown', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('algorithm_version', sa.Text(), nullable=False),
        sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(
            ['repository_id'], ['repositories.id'],
            name=op.f('fk_repository_scores_repository_id_repositories'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_repository_scores')),
    )

    op.create_index(
        op.f('ix_repository_scores_repository_id_score_type_computed_at'),
        'repository_scores',
        ['repository_id', 'score_type', sa.text('computed_at DESC')],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_repository_scores_repository_id_score_type_computed_at'), table_name='repository_scores')
    op.drop_table('repository_scores')

    postgresql.ENUM(name='score_type').drop(op.get_bind(), checkfirst=True)
