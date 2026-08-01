"""005_repository_snapshot_history

Creates repository_snapshot: cheap, high-frequency raw GitHub counters
(stars, forks, watchers, open issues, size_kb) captured at each
observation point - deliberately separate from the more expensive
computed feature store (repository_metrics, Migration 006).

Own migration because it is owned by a different module (Data
Acquisition, not Feature Engineering) and has a different write cadence
than the metrics/scores tables that follow. Kept independent of
repository_metrics to avoid coupling cheap/frequent raw-counter capture
to expensive/infrequent feature computation (DATABASE_DESIGN.md Sec2.9).

size_kb is this table's sole authoritative home (DATABASE_ARCHITECT_REVIEW.md
Finding 2) - removed from repositories in Migration 002. Default
observation cadence is weekly, not daily (Finding 5) - reduces this
table's growth rate 7x with no loss of supported query capability.

watchers_count is retained per Finding 3 but documented (not enforced in
schema, since this is a data-interpretation note, not a constraint) as a
near-duplicate of stars_count - not to be treated as an independent
popularity signal in any future scoring formula.

Schema dependency: only repositories.id (Migration 002) - no relationship
to licenses/topics/technologies (001), the association tables (003), or
repository_dependencies (004). Alembic chain remains linear (004 -> 005)
for review-ordering only.

Index note: QUERY_DRIVEN_SCHEMA_DESIGN.md Sec14.10 lists both
UNIQUE(repository_id, snapshot_date) and INDEX(repository_id,
snapshot_date) with identical columns/order and no distinguishing detail
(unlike repository_metrics' analogous section, which explicitly uses
DESC to differentiate its two index entries). Per user confirmation,
only the UNIQUE constraint is created here - its auto-generated backing
index already serves the stated range-scan/trend-query purpose, and a
second physically-identical index would be pure redundant overhead.

Declared (not implemented) future partition key per
DATABASE_ARCHITECT_REVIEW.md Finding 9: snapshot_date (RANGE, monthly or
quarterly), coordinated with repository_metrics (Migration 006) as one
future initiative. No CREATE TABLE ... PARTITION BY is created here.

Revision ID: 005
Revises: 004
Create Date: 2026-08-01

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '005'
down_revision = '004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'repository_snapshot',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column('repository_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('snapshot_date', sa.Date(), nullable=False),
        sa.Column('stars_count', sa.Integer(), nullable=False),
        sa.Column('forks_count', sa.Integer(), nullable=False),
        sa.Column('watchers_count', sa.Integer(), nullable=False),
        sa.Column('open_issues_count', sa.Integer(), nullable=False),
        sa.Column('size_kb', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ['repository_id'], ['repositories.id'],
            name=op.f('fk_repository_snapshot_repository_id_repositories'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_repository_snapshot')),
        sa.UniqueConstraint(
            'repository_id', 'snapshot_date',
            name=op.f('uq_repository_snapshot_repository_id_snapshot_date'),
        ),
    )


def downgrade() -> None:
    op.drop_table('repository_snapshot')
