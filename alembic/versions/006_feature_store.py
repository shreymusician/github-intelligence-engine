"""006_feature_store

Creates repository_metrics: the platform's feature store - computed,
engineered features (activity, documentation quality, code size,
dependency health rollups) described in TECHNICAL_IMPLEMENTATION_IDEAS.md
Sec5 and REPOSITORY_INTELLIGENCE_ENGINE.md Sec2.

Deliberately separate from repository_snapshot (Migration 005):
repository_snapshot holds cheap, raw GitHub counters fetched at near-zero
cost with every API call; repository_metrics holds expensive, computed
features requiring actual analysis work. Conflating them would force
expensive recomputation just to log a star-count delta (DATABASE_DESIGN.md
Sec2.8 vs Sec2.9).

Own migration because this is the largest single table definition in the
schema (27 typed columns + 2 JSONB) and the one most likely to accumulate
new columns as Milestone 2's checkpoints (2.1 Activity, 2.2 Technology,
2.3 Documentation, 2.4 Code Complexity, 2.5 Dependency) each contribute
their own feature groups over time. Isolating it means future
column-addition migrations have a clean, singular target.

Schema dependency: only repositories.id (Migration 002) - no relationship
to Migrations 001, 003, 004, or 005. Alembic chain position 005->006 is
linear-ordering only, same clarification pattern as prior migration
reports.

top_contributor_commit_share has a CHECK (0-1 range) per Phase 2 Category F
finding, directly analogous to the confidence CHECK pattern established in
Migration 003.

Two JSONB columns, each individually justified per QUERY_DRIVEN_SCHEMA_
DESIGN.md Sec14.9: loc_by_language (genuinely variable-shaped, unbounded
key set) and extended_features (deliberate overflow bucket for long-tail
features not yet promoted to columns, backed by a GIN index for ad-hoc
querying without requiring a migration per new experimental feature).

Indexes (Sec14.9, unambiguous - unlike Migration 005's index question,
the DESC modifier here explicitly differentiates the two btree entries):
- UNIQUE (repository_id, snapshot_date): one metrics snapshot per repo
  per day, prevents duplicate computation runs.
- INDEX (repository_id, snapshot_date DESC): "latest metrics per
  repository" access path, used by nearly every intelligence and search
  query (Category B, C, F, J) - also named explicitly in Sec14's Access
  Pattern table.
- GIN INDEX (extended_features): ad-hoc querying into the JSONB overflow
  bucket.

Declared (not implemented) future partition key per
DATABASE_ARCHITECT_REVIEW.md Finding 9: snapshot_date (RANGE, monthly or
quarterly), coordinated with repository_snapshot (Migration 005) as one
future initiative. No CREATE TABLE ... PARTITION BY is created here.

Revision ID: 006
Revises: 005
Create Date: 2026-08-01

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '006'
down_revision = '005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'repository_metrics',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column('repository_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('snapshot_date', sa.Date(), nullable=False),
        sa.Column('commits_last_3mo', sa.Integer(), nullable=True),
        sa.Column('commits_last_6mo', sa.Integer(), nullable=True),
        sa.Column('commits_last_12mo', sa.Integer(), nullable=True),
        sa.Column('unique_contributors_last_12mo', sa.Integer(), nullable=True),
        sa.Column('top_contributor_commit_share', sa.Numeric(4, 3), nullable=True),
        sa.Column('days_since_last_commit', sa.Integer(), nullable=True),
        sa.Column('commit_frequency_stddev', sa.Numeric(), nullable=True),
        sa.Column('issues_open_count', sa.Integer(), nullable=True),
        sa.Column('issues_closed_count', sa.Integer(), nullable=True),
        sa.Column('issue_close_rate', sa.Numeric(4, 3), nullable=True),
        sa.Column('avg_issue_close_days', sa.Numeric(), nullable=True),
        sa.Column('prs_merged_count', sa.Integer(), nullable=True),
        sa.Column('pr_merge_rate', sa.Numeric(4, 3), nullable=True),
        sa.Column('avg_pr_merge_hours', sa.Numeric(), nullable=True),
        sa.Column('readme_present', sa.Boolean(), nullable=True),
        sa.Column('readme_word_count', sa.Integer(), nullable=True),
        sa.Column('readme_section_count', sa.Integer(), nullable=True),
        sa.Column('readme_code_example_count', sa.Integer(), nullable=True),
        sa.Column('readme_readability_score', sa.Numeric(), nullable=True),
        sa.Column('loc_total', sa.Integer(), nullable=True),
        sa.Column('test_file_ratio', sa.Numeric(4, 3), nullable=True),
        sa.Column('comment_to_code_ratio', sa.Numeric(4, 3), nullable=True),
        sa.Column('direct_dependency_count', sa.Integer(), nullable=True),
        sa.Column('transitive_dependency_count', sa.Integer(), nullable=True),
        sa.Column('outdated_dependency_pct', sa.Numeric(4, 3), nullable=True),
        sa.Column('loc_by_language', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('extended_features', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(
            ['repository_id'], ['repositories.id'],
            name=op.f('fk_repository_metrics_repository_id_repositories'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_repository_metrics')),
        sa.UniqueConstraint(
            'repository_id', 'snapshot_date',
            name=op.f('uq_repository_metrics_repository_id_snapshot_date'),
        ),
        sa.CheckConstraint(
            'top_contributor_commit_share >= 0 AND top_contributor_commit_share <= 1',
            name=op.f('ck_repository_metrics_top_contributor_commit_share_range'),
        ),
    )

    op.create_index(
        op.f('ix_repository_metrics_repository_id_snapshot_date'),
        'repository_metrics',
        ['repository_id', sa.text('snapshot_date DESC')],
        unique=False,
    )
    op.create_index(
        op.f('ix_repository_metrics_extended_features'),
        'repository_metrics',
        ['extended_features'],
        unique=False,
        postgresql_using='gin',
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_repository_metrics_extended_features'), table_name='repository_metrics')
    op.drop_index(op.f('ix_repository_metrics_repository_id_snapshot_date'), table_name='repository_metrics')
    op.drop_table('repository_metrics')
