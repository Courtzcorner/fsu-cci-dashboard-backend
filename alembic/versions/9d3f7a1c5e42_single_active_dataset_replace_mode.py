"""single active dataset replace mode

Adds the fields required for CSV-import "replace mode": every successful
import becomes the complete, exclusive active dataset for its
organization, and alumni rows not present in the newest import are
deactivated (never physically deleted).

- alumni.email: used (after normalized LinkedIn URL) to match a CSV row to
  an existing alumni record on reimport, so the same person is never
  recreated as a duplicate.
- alumni.is_active: True for alumni in the current/latest successful
  import, False for alumni superseded by a newer import. Defaults to True
  and backfills all existing rows as active so nothing already in the
  database silently disappears from GET /alumni-data until the next import
  runs.
- alumni.source_import_id: nullable FK to csv_imports.id identifying which
  import most recently created/confirmed this alumni row as active.

Revision ID: 9d3f7a1c5e42
Revises: 3c5ca66e196c
Create Date: 2026-07-27 12:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9d3f7a1c5e42'
down_revision: Union[str, None] = '3c5ca66e196c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('alumni', sa.Column('email', sa.String(length=255), nullable=True))
    op.create_index(op.f('ix_alumni_email'), 'alumni', ['email'], unique=False)

    # server_default=true() makes this a safe, non-destructive backfill:
    # every existing row becomes active=True without a separate UPDATE
    # statement or table rewrite.
    op.add_column(
        'alumni',
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index(op.f('ix_alumni_is_active'), 'alumni', ['is_active'], unique=False)

    op.add_column('alumni', sa.Column('source_import_id', sa.String(length=36), nullable=True))
    op.create_index(op.f('ix_alumni_source_import_id'), 'alumni', ['source_import_id'], unique=False)
    op.create_foreign_key(
        'fk_alumni_source_import_id',
        'alumni',
        'csv_imports',
        ['source_import_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('fk_alumni_source_import_id', 'alumni', type_='foreignkey')
    op.drop_index(op.f('ix_alumni_source_import_id'), table_name='alumni')
    op.drop_column('alumni', 'source_import_id')

    op.drop_index(op.f('ix_alumni_is_active'), table_name='alumni')
    op.drop_column('alumni', 'is_active')

    op.drop_index(op.f('ix_alumni_email'), table_name='alumni')
    op.drop_column('alumni', 'email')
