"""profile quick-education fields + effective-data cache columns

Adds the columns needed for the "effective alumni data" layer:

- user_profiles.degree / field_of_study / graduation_year: a quick,
  top-level education summary (independent of the multi-entry
  user_education_history table).
- user_profiles.current_industry: the ONLY user-supplied industry
  source (never guessed from current_employer).
- user_profiles.effective_full_name / effective_seniority(+source) /
  effective_career_category(+source) / effective_industry(+source):
  a write-time cache, recomputed on every profile save (see
  app.services.effective_profile_service), that the SQL-level effective
  data layer (app.services.effective_alumni_service) joins against via
  a plain CASE/COALESCE expression - never a per-row Python computation
  at read time.

None of these columns touch `alumni`, `csv_imports`, or any other table
owned by the CSV import pipeline.

Revision ID: f2b3c4d5e6a7
Revises: e1a2b3c4d5f6
Create Date: 2026-07-27 22:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2b3c4d5e6a7'
down_revision: Union[str, None] = 'e1a2b3c4d5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('user_profiles', sa.Column('degree', sa.String(length=255), nullable=True))
    op.add_column('user_profiles', sa.Column('field_of_study', sa.String(length=255), nullable=True))
    op.add_column('user_profiles', sa.Column('graduation_year', sa.Integer(), nullable=True))
    op.add_column('user_profiles', sa.Column('current_industry', sa.String(length=255), nullable=True))

    op.add_column('user_profiles', sa.Column('effective_full_name', sa.String(length=256), nullable=True))
    op.add_column('user_profiles', sa.Column('effective_seniority', sa.String(length=64), nullable=True))
    op.add_column('user_profiles', sa.Column('effective_seniority_source', sa.String(length=32), nullable=True))
    op.add_column('user_profiles', sa.Column('effective_career_category', sa.String(length=255), nullable=True))
    op.add_column(
        'user_profiles', sa.Column('effective_career_category_source', sa.String(length=32), nullable=True)
    )
    op.add_column('user_profiles', sa.Column('effective_industry', sa.String(length=255), nullable=True))
    op.add_column('user_profiles', sa.Column('effective_industry_source', sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column('user_profiles', 'effective_industry_source')
    op.drop_column('user_profiles', 'effective_industry')
    op.drop_column('user_profiles', 'effective_career_category_source')
    op.drop_column('user_profiles', 'effective_career_category')
    op.drop_column('user_profiles', 'effective_seniority_source')
    op.drop_column('user_profiles', 'effective_seniority')
    op.drop_column('user_profiles', 'effective_full_name')

    op.drop_column('user_profiles', 'current_industry')
    op.drop_column('user_profiles', 'graduation_year')
    op.drop_column('user_profiles', 'field_of_study')
    op.drop_column('user_profiles', 'degree')
