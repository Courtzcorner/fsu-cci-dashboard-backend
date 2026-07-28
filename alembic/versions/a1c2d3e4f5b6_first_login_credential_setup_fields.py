"""first-login credential setup fields on users

Additive-only columns on `users` for the temporary-account /
forced-credential-setup flow:

- must_change_credentials (default False - no existing user is affected)
- temporary_account_created_at / credentials_updated_at
- previous_username / username_changed_at (audit trail for the one-time
  username change performed during setup)
- token_version (default 0 - minimal additive JWT "tv" claim used to
  revoke a temporary session / implement logout without a server-side
  token store)

Nothing here touches `alumni`, `user_profiles`, `csv_imports`, or any
other table - and no existing row's authentication behavior changes,
since every new boolean/int column defaults to its "normal account"
value.

Revision ID: a1c2d3e4f5b6
Revises: f2b3c4d5e6a7
Create Date: 2026-07-28 09:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1c2d3e4f5b6'
down_revision: Union[str, None] = 'f2b3c4d5e6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('must_change_credentials', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column('users', sa.Column('temporary_account_created_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('credentials_updated_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('previous_username', sa.String(length=128), nullable=True))
    op.add_column('users', sa.Column('username_changed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('token_version', sa.Integer(), nullable=False, server_default='0'))

    # Drop the server defaults after backfilling existing rows - new rows
    # rely on the ORM-level Python defaults instead, matching the rest of
    # this codebase's convention.
    op.alter_column('users', 'must_change_credentials', server_default=None)
    op.alter_column('users', 'token_version', server_default=None)


def downgrade() -> None:
    op.drop_column('users', 'token_version')
    op.drop_column('users', 'username_changed_at')
    op.drop_column('users', 'previous_username')
    op.drop_column('users', 'credentials_updated_at')
    op.drop_column('users', 'temporary_account_created_at')
    op.drop_column('users', 'must_change_credentials')
