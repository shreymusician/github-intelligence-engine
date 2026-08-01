"""001_reference_tables

Creates the reference/taxonomy tables that have no foreign keys into the
core entity graph yet: licenses, topics, technologies. Per
MIGRATION_STRATEGY.md, this is the first migration against Checkpoint 0.1's
empty database and depends on nothing but the pre-installed uuid-ossp
extension.

Revision ID: 001
Revises:
Create Date: 2026-07-31

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    technology_category = postgresql.ENUM(
        'language', 'framework', 'database', 'testing_tool', 'devops_tool', 'platform',
        name='technology_category',
    )

    op.create_table(
        'licenses',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('uuid_generate_v4()'), nullable=False),
        sa.Column('spdx_id', sa.Text(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('is_osi_approved', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_licenses')),
        sa.UniqueConstraint('spdx_id', name=op.f('uq_licenses_spdx_id')),
    )

    op.create_table(
        'topics',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('uuid_generate_v4()'), nullable=False),
        sa.Column('slug', sa.Text(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_topics')),
        sa.UniqueConstraint('slug', name=op.f('uq_topics_slug')),
    )

    op.create_table(
        'technologies',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('uuid_generate_v4()'), nullable=False),
        sa.Column('slug', sa.Text(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('category', technology_category, nullable=False),
        sa.Column('homepage_url', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_technologies')),
        sa.UniqueConstraint('slug', name=op.f('uq_technologies_slug')),
    )
    op.create_index(op.f('ix_technologies_category'), 'technologies', ['category'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_technologies_category'), table_name='technologies')
    op.drop_table('technologies')
    op.drop_table('topics')
    op.drop_table('licenses')

    postgresql.ENUM(name='technology_category').drop(op.get_bind(), checkfirst=True)
