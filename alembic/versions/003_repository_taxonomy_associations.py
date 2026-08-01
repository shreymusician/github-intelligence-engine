"""003_repository_taxonomy_associations

Creates the M:N join tables connecting repositories to the two
Migration-001 taxonomy tables: repository_topics (pure join, no edge
attributes) and repository_technologies (associative entity, edge
carries role/confidence/detection-method attributes).

Grouped together per MIGRATION_STRATEGY.md because both share the same
shape ("what is this repository tagged/built with") and depend on the
exact same prerequisite state (001 + 002) - splitting them into two
migrations would add process overhead with no independence benefit.

Kept separate from 004_repository_dependencies because that table is
NOT a join to a curated reference table - it is high-cardinality,
independently-owned package data and a future partitioning candidate.

repository_technologies.removed_at (nullable) is included per
DATABASE_ARCHITECT_REVIEW.md Finding 6: without it, a technology adopted
then later abandoned across many repositories would show inflated,
indefinitely-growing "adoption" counts in trend queries, since
first_detected_at alone only records when an association started.

Revision ID: 003
Revises: 002
Create Date: 2026-08-01

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '003'
down_revision = '002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    technology_role = postgresql.ENUM(
        'primary', 'secondary', 'dev',
        name='technology_role',
    )
    detected_via = postgresql.ENUM(
        'manifest', 'dockerfile', 'ci_config', 'linguist',
        name='detected_via',
    )

    # -------------------------------------------------------------------
    # repository_topics
    # -------------------------------------------------------------------
    op.create_table(
        'repository_topics',
        sa.Column('repository_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('topic_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['repository_id'], ['repositories.id'],
            name=op.f('fk_repository_topics_repository_id_repositories'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['topic_id'], ['topics.id'],
            name=op.f('fk_repository_topics_topic_id_topics'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('repository_id', 'topic_id', name=op.f('pk_repository_topics')),
    )
    op.create_index(
        op.f('ix_repository_topics_topic_id'), 'repository_topics', ['topic_id'], unique=False
    )

    # -------------------------------------------------------------------
    # repository_technologies
    # -------------------------------------------------------------------
    op.create_table(
        'repository_technologies',
        sa.Column('repository_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('technology_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('role', technology_role, nullable=False),
        sa.Column('detected_via', detected_via, nullable=False),
        sa.Column('confidence', sa.Numeric(3, 2), nullable=False),
        sa.Column('first_detected_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_confirmed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('removed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ['repository_id'], ['repositories.id'],
            name=op.f('fk_repository_technologies_repository_id_repositories'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['technology_id'], ['technologies.id'],
            name=op.f('fk_repository_technologies_technology_id_technologies'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('repository_id', 'technology_id', name=op.f('pk_repository_technologies')),
        sa.CheckConstraint(
            'confidence >= 0 AND confidence <= 1',
            name=op.f('ck_repository_technologies_confidence_range'),
        ),
    )
    op.create_index(
        op.f('ix_repository_technologies_technology_id_role'),
        'repository_technologies',
        ['technology_id', 'role'],
        unique=False,
    )
    op.create_index(
        op.f('ix_repository_technologies_current'),
        'repository_technologies',
        ['technology_id'],
        unique=False,
        postgresql_where=sa.text('removed_at IS NULL'),
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_repository_technologies_current'), table_name='repository_technologies')
    op.drop_index(op.f('ix_repository_technologies_technology_id_role'), table_name='repository_technologies')
    op.drop_table('repository_technologies')

    postgresql.ENUM(name='detected_via').drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name='technology_role').drop(op.get_bind(), checkfirst=True)

    op.drop_index(op.f('ix_repository_topics_topic_id'), table_name='repository_topics')
    op.drop_table('repository_topics')
