"""002_core_entities

Creates the central entity (repositories) and its one mandatory parent
(owners). Per MIGRATION_STRATEGY.md, kept as its own migration (not merged
with 001) because repositories is the highest-traffic table in the schema
and is expected to evolve independently of the static reference tables.

repositories.owner_id is NOT NULL -> owners must exist first, within this
same migration. repositories.license_id is a nullable FK into licenses
(created in 001), so this migration depends on 001 having run.

Revised column set per DATABASE_ARCHITECT_REVIEW.md (Findings 1, 2, 4, 12):
- default_branch, homepage_url, is_template, size_kb, first_seen_at are
  EXCLUDED (unjustified GitHub-mirror / duplicate columns).
- is_accessible, inaccessible_since are INCLUDED (repository-lifecycle gap
  fix: "gone from GitHub" is a distinct state from is_archived (maintainer
  action) and last_extraction_status (pipeline outcome)).

Revision ID: 002
Revises: 001
Create Date: 2026-08-01

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '002'
down_revision = '001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    account_type = postgresql.ENUM(
        'user', 'organization',
        name='account_type',
    )
    last_extraction_status = postgresql.ENUM(
        'pending', 'success', 'failed',
        name='last_extraction_status',
    )

    # -------------------------------------------------------------------
    # owners
    # -------------------------------------------------------------------
    op.create_table(
        'owners',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('uuid_generate_v4()'), nullable=False),
        sa.Column('github_id', sa.BigInteger(), nullable=False),
        sa.Column('login', sa.Text(), nullable=False),
        sa.Column('account_type', account_type, nullable=False),
        sa.Column('avatar_url', sa.Text(), nullable=True),
        sa.Column('github_created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_owners')),
        sa.UniqueConstraint('github_id', name=op.f('uq_owners_github_id')),
    )
    op.create_index(op.f('ix_owners_login'), 'owners', ['login'], unique=False)

    # -------------------------------------------------------------------
    # repositories
    # -------------------------------------------------------------------
    op.create_table(
        'repositories',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('uuid_generate_v4()'), nullable=False),
        sa.Column('github_id', sa.BigInteger(), nullable=False),
        sa.Column('owner_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('full_name', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('primary_language', sa.Text(), nullable=True),
        sa.Column('license_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('is_archived', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('is_fork', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('is_accessible', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('inaccessible_since', sa.DateTime(timezone=True), nullable=True),
        sa.Column('github_created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('pushed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('fetched_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_extraction_status', last_extraction_status, server_default='pending', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(
            ['owner_id'], ['owners.id'],
            name=op.f('fk_repositories_owner_id_owners'),
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['license_id'], ['licenses.id'],
            name=op.f('fk_repositories_license_id_licenses'),
            ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_repositories')),
        sa.UniqueConstraint('github_id', name=op.f('uq_repositories_github_id')),
        sa.UniqueConstraint('full_name', name=op.f('uq_repositories_full_name')),
    )

    op.create_index(op.f('ix_repositories_owner_id'), 'repositories', ['owner_id'], unique=False)
    op.create_index(op.f('ix_repositories_primary_language'), 'repositories', ['primary_language'], unique=False)
    op.create_index(op.f('ix_repositories_license_id'), 'repositories', ['license_id'], unique=False)
    op.create_index(op.f('ix_repositories_fetched_at'), 'repositories', ['fetched_at'], unique=False)

    # Partial index: the extremely common "active, original work" default filter
    op.create_index(
        op.f('ix_repositories_active_original'),
        'repositories',
        ['is_archived', 'is_fork'],
        unique=False,
        postgresql_where=sa.text('is_archived = false AND is_fork = false'),
    )

    # Partial index: cheaply exclude inaccessible repos from default queries
    op.create_index(
        op.f('ix_repositories_inaccessible'),
        'repositories',
        ['is_accessible'],
        unique=False,
        postgresql_where=sa.text('is_accessible = false'),
    )

    # Full-text search index (keyword search + hybrid search Milestone 4.1)
    op.create_index(
        op.f('ix_repositories_fts'),
        'repositories',
        [sa.text("to_tsvector('english', name || ' ' || description)")],
        unique=False,
        postgresql_using='gin',
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_repositories_fts'), table_name='repositories')
    op.drop_index(op.f('ix_repositories_inaccessible'), table_name='repositories')
    op.drop_index(op.f('ix_repositories_active_original'), table_name='repositories')
    op.drop_index(op.f('ix_repositories_fetched_at'), table_name='repositories')
    op.drop_index(op.f('ix_repositories_license_id'), table_name='repositories')
    op.drop_index(op.f('ix_repositories_primary_language'), table_name='repositories')
    op.drop_index(op.f('ix_repositories_owner_id'), table_name='repositories')
    op.drop_table('repositories')

    postgresql.ENUM(name='last_extraction_status').drop(op.get_bind(), checkfirst=True)

    op.drop_index(op.f('ix_owners_login'), table_name='owners')
    op.drop_table('owners')

    postgresql.ENUM(name='account_type').drop(op.get_bind(), checkfirst=True)
