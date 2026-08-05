"""organization context metadata + user-organization memberships

Additive-only, Phase 1 multi-institution infrastructure:

  - `organizations.context_type` ("national" | "institution") and
    `organizations.theme_key` (nullable) - presentation/filtering
    metadata only, never the sole authorization check.
  - New `user_organizations` table: explicit per-organization membership
    for a login account, with an optional per-org `role` override (null
    means "inherit users.role"). Mirrors the existing `alumni_organizations`
    many-to-many pattern, but for login accounts instead of alumni
    records.

No existing table/column is modified, dropped, or renamed, and no data is
backfilled by this migration - it creates the schema only. Nothing yet
depends on this table (no existing endpoint's authorization changes as a
result of this migration alone).

Revision ID: 90745d7d8acb
Revises: c3d4e5f6a7b8
Create Date: 2026-08-05 11:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '90745d7d8acb'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default only long enough to backfill the 3 existing rows on
    # ADD COLUMN - new rows rely on the ORM-level Python default instead,
    # matching this codebase's convention (see c3d4e5f6a7b8).
    op.add_column(
        'organizations',
        sa.Column('context_type', sa.String(length=32), nullable=False, server_default='institution'),
    )
    op.add_column('organizations', sa.Column('theme_key', sa.String(length=64), nullable=True))
    op.alter_column('organizations', 'context_type', server_default=None)

    op.create_table(
        'user_organizations',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('organization_id', sa.String(length=36), nullable=False),
        sa.Column('role', sa.String(length=32), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('user_id', 'organization_id', name='uq_user_organization'),
    )
    op.create_index('ix_user_organizations_user_id', 'user_organizations', ['user_id'])
    op.create_index('ix_user_organizations_organization_id', 'user_organizations', ['organization_id'])


def downgrade() -> None:
    op.drop_index('ix_user_organizations_organization_id', table_name='user_organizations')
    op.drop_index('ix_user_organizations_user_id', table_name='user_organizations')
    op.drop_table('user_organizations')
    op.drop_column('organizations', 'theme_key')
    op.drop_column('organizations', 'context_type')
