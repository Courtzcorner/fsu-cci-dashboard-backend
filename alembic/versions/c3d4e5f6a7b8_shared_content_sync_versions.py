"""shared content synchronization version table

Additive-only: a single new table, `content_versions`, backing the new
GET /sync/status endpoint (see app.models.content_version /
app.services.content_version_service). No existing table or column is
modified, dropped, or renamed.

One row per content domain (alumni, analytics, locations, events,
superstars, speakers, universities, profiles) plus a "global" row,
incremented in place by admin mutations (CSV import, event/speaker/
superstar create-update-delete, profile-link moderation, and a
confirmed/linked profile's own effective-data edits) so every logged-in
client - regardless of when its session started - can cheaply detect
that shared data changed without recalculating analytics or loading
alumni rows.

Revision ID: c3d4e5f6a7b8
Revises: a1c2d3e4f5b6
Create Date: 2026-07-28 11:35:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'a1c2d3e4f5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'content_versions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('domain', sa.String(length=32), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_by_user_id', sa.String(length=36), nullable=True),
        sa.Column('change_type', sa.String(length=64), nullable=True),
        sa.Column('resource_id', sa.String(length=36), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['updated_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.UniqueConstraint('domain', name='uq_content_versions_domain'),
    )
    op.create_index('ix_content_versions_domain', 'content_versions', ['domain'])

    # Drop the server default after table creation - new rows rely on the
    # ORM-level Python default instead, matching this codebase's convention.
    op.alter_column('content_versions', 'version', server_default=None)


def downgrade() -> None:
    op.drop_index('ix_content_versions_domain', table_name='content_versions')
    op.drop_table('content_versions')
