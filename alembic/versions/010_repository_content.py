"""010_repository_content

Creates repository_content: durable PostgreSQL persistence for the
README and package-manifest text already extracted by Checkpoint 1.4.b
(acquisition/content_extractor.py's ExtractedFile objects). This table's
schema was introduced and approved as part of the Checkpoint 1.4 design
specification (Repository Content Download) - it postdates and is not
part of DATABASE_DESIGN.md's frozen Checkpoint 0.2 twelve-entity model.
DATABASE_DESIGN.md is intentionally left unchanged; this migration and
CHECKPOINT_1_4C_REPORT.md are its provenance record.

Owning module: Data Acquisition (matches DATABASE_DESIGN.md Sec5's
ownership convention for entities written by Module 1), specifically the
1.4.c ContentWriter (acquisition/content_storage.py) - the persistence
boundary for the ExtractedFile -> repository_content flow. This
migration does not clone repositories, discover files, or extract
content; it only creates the table those upstream 1.4.a/1.4.b steps
write into.

Columns match the approved schema exactly:
- id UUID primary key
- repository_id UUID FK -> repositories(id) ON DELETE CASCADE
- content_type TEXT
- file_path TEXT
- content TEXT
- content_hash TEXT
- content_size_bytes INTEGER, CHECK (content_size_bytes >= 0)
- fetched_at TIMESTAMPTZ
- created_at TIMESTAMPTZ
- updated_at TIMESTAMPTZ
- UNIQUE(repository_id, file_path)

No secondary indexes beyond the PK and the UNIQUE(repository_id,
file_path) constraint (which Postgres indexes automatically) - no query
in this checkpoint's scope justifies one, and none is spec­ulatively
added here.

Idempotency semantics (enforced in acquisition/content_storage.py's
upsert, not in this migration): ON CONFLICT (repository_id, file_path)
DO UPDATE refreshes content/content_hash/content_size_bytes/fetched_at
unconditionally, but only bumps updated_at when content_hash actually
changed (via a CASE ... IS DISTINCT FROM expression) - preserving
updated_at's meaning as "last time the content itself changed," not
"last time this row was touched."

Revision ID: 010
Revises: 009
Create Date: 2026-08-12

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '010'
down_revision = '009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'repository_content',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('uuid_generate_v4()'), nullable=False),
        sa.Column('repository_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('content_type', sa.Text(), nullable=False),
        sa.Column('file_path', sa.Text(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('content_hash', sa.Text(), nullable=False),
        sa.Column('content_size_bytes', sa.Integer(), nullable=False),
        sa.Column('fetched_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(
            ['repository_id'], ['repositories.id'],
            name=op.f('fk_repository_content_repository_id_repositories'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_repository_content')),
        sa.UniqueConstraint(
            'repository_id', 'file_path',
            name=op.f('uq_repository_content_repository_id_file_path'),
        ),
        sa.CheckConstraint(
            'content_size_bytes >= 0',
            name=op.f('ck_repository_content_content_size_bytes_non_negative'),
        ),
    )


def downgrade() -> None:
    op.drop_table('repository_content')
