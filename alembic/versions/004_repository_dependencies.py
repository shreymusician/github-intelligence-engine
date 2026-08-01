"""004_repository_dependencies

Creates repository_dependencies: fine-grained, package-level dependency
data (exact package name, ecosystem, version, staleness, vulnerability
count) - deliberately distinct from the curated repository_technologies
taxonomy (Migration 003).

Kept separate from 003 despite superficial similarity (also a
repository-scoped, many-rows-per-repo table) because this is NOT a join
to a curated reference table - it is high-cardinality, independently-owned
package-registry-derived data. Per DATABASE_ARCHITECT_REVIEW.md Finding 9,
this is the schema's highest-volume table by repository-count growth
alone (avg. 30-100 dependencies per repository) and its first
partitioning candidate.

Schema dependency: only repositories.id (Migration 002) - this table has
no relationship to licenses/topics/technologies (001) or the association
tables (003). The Alembic revision chain remains linear per
MIGRATION_STRATEGY.md's sequencing diagram regardless.

Declared (not implemented) future partition key per
DATABASE_ARCHITECT_REVIEW.md Finding 9 / QUERY_DRIVEN_SCHEMA_DESIGN.md
Sec16: repository_id (HASH) or ecosystem (LIST) - growth is driven by
repository count, not time, so no date-range partition key applies here
(unlike repository_metrics/snapshot/scores in Migrations 006-007). No
CREATE TABLE ... PARTITION BY is created in this migration; this is
documentation only, per the frozen design's explicit YAGNI stance.

Revision ID: 004
Revises: 003
Create Date: 2026-08-01

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '004'
down_revision = '003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    package_ecosystem = postgresql.ENUM(
        'npm', 'pypi', 'cargo', 'go', 'maven', 'nuget', 'rubygems',
        name='package_ecosystem',
    )

    op.create_table(
        'repository_dependencies',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column('repository_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('package_name', sa.Text(), nullable=False),
        sa.Column('ecosystem', package_ecosystem, nullable=False),
        sa.Column('version_constraint', sa.Text(), nullable=True),
        sa.Column('resolved_version', sa.Text(), nullable=True),
        sa.Column('is_direct', sa.Boolean(), nullable=False),
        sa.Column('is_dev', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('dependency_age_days', sa.Integer(), nullable=True),
        sa.Column('is_deprecated', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('vulnerability_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('last_checked_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(
            ['repository_id'], ['repositories.id'],
            name=op.f('fk_repository_dependencies_repository_id_repositories'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_repository_dependencies')),
    )

    op.create_index(
        op.f('ix_repository_dependencies_repository_id'),
        'repository_dependencies', ['repository_id'], unique=False,
    )
    op.create_index(
        op.f('ix_repository_dependencies_package_name_ecosystem'),
        'repository_dependencies', ['package_name', 'ecosystem'], unique=False,
    )
    op.create_index(
        op.f('ix_repository_dependencies_vulnerable'),
        'repository_dependencies', ['repository_id'], unique=False,
        postgresql_where=sa.text('vulnerability_count > 0'),
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_repository_dependencies_vulnerable'), table_name='repository_dependencies')
    op.drop_index(op.f('ix_repository_dependencies_package_name_ecosystem'), table_name='repository_dependencies')
    op.drop_index(op.f('ix_repository_dependencies_repository_id'), table_name='repository_dependencies')
    op.drop_table('repository_dependencies')

    postgresql.ENUM(name='package_ecosystem').drop(op.get_bind(), checkfirst=True)
