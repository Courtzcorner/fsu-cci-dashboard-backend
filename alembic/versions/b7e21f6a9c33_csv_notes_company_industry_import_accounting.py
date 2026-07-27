"""notes, company industry mapping, csv import row accounting

Adds the columns needed to:

- carry the CSV's free-text "Notes" column through to the Alumni record
  (alumni.notes)
- support a verified, non-AI company -> industry mapping as the second
  deterministic industry source, after a nonblank imported Industry
  column (companies.industry)
- persist row-accounting (rows_received/rows_valid/rows_invalid) on each
  CSVImport so GET /admin/current-import can report a past import's
  provenance without needing the original request/response

Revision ID: b7e21f6a9c33
Revises: 9d3f7a1c5e42
Create Date: 2026-07-27 15:35:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7e21f6a9c33'
down_revision: Union[str, None] = '9d3f7a1c5e42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('alumni', sa.Column('notes', sa.String(length=4000), nullable=True))

    op.add_column('companies', sa.Column('industry', sa.String(length=255), nullable=True))

    op.add_column('csv_imports', sa.Column('rows_received', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('csv_imports', sa.Column('rows_valid', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('csv_imports', sa.Column('rows_invalid', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('csv_imports', 'rows_invalid')
    op.drop_column('csv_imports', 'rows_valid')
    op.drop_column('csv_imports', 'rows_received')

    op.drop_column('companies', 'industry')

    op.drop_column('alumni', 'notes')
