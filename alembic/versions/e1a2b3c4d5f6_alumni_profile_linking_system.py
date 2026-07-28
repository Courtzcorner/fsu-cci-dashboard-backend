"""alumni account profile and directory linking system

Adds the self-service "user profile" layer as brand-new, additive
tables. Nothing here alters `alumni`, `csv_imports`, `companies`, or any
other table owned by the CSV import pipeline:

- user_profiles: one row per user account (users.id), holding
  user-supplied profile data (general/contact/personal/social/speaking)
  plus the optional, reviewable link to an imported Alumni record
  (alumni_id/link_status/link_confidence/linked_at/linked_by/
  match_evidence/needs_review).
- user_work_history / user_education_history: multi-entry child tables
  for a user profile's work/education history.
- profile_match_candidates: persisted, transparent output of the
  deterministic identity matcher (app.services.identity_matching_service)
  - the audit trail behind every link.

Revision ID: e1a2b3c4d5f6
Revises: b7e21f6a9c33
Create Date: 2026-07-27 21:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1a2b3c4d5f6'
down_revision: Union[str, None] = 'b7e21f6a9c33'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'user_profiles',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('profile_photo_url', sa.String(length=512), nullable=True),
        sa.Column('first_name', sa.String(length=128), nullable=True),
        sa.Column('last_name', sa.String(length=128), nullable=True),
        sa.Column('preferred_name', sa.String(length=128), nullable=True),
        sa.Column('headline', sa.String(length=255), nullable=True),
        sa.Column('current_city', sa.String(length=128), nullable=True),
        sa.Column('current_state', sa.String(length=64), nullable=True),
        sa.Column('current_country', sa.String(length=128), nullable=True),
        sa.Column('current_job_title', sa.String(length=255), nullable=True),
        sa.Column('current_employer', sa.String(length=255), nullable=True),
        sa.Column('current_university', sa.String(length=255), nullable=True),
        sa.Column('bio', sa.Text(), nullable=True),
        sa.Column('primary_email', sa.String(length=255), nullable=True),
        sa.Column('secondary_email', sa.String(length=255), nullable=True),
        sa.Column('phone_number', sa.String(length=32), nullable=True),
        sa.Column('personal_website', sa.String(length=512), nullable=True),
        sa.Column('birthday', sa.Date(), nullable=True),
        sa.Column('pronouns', sa.String(length=64), nullable=True),
        sa.Column('hometown', sa.String(length=255), nullable=True),
        sa.Column('linkedin_url', sa.String(length=512), nullable=True),
        sa.Column('github_url', sa.String(length=512), nullable=True),
        sa.Column('instagram_url', sa.String(length=512), nullable=True),
        sa.Column('x_url', sa.String(length=512), nullable=True),
        sa.Column('personal_website_url', sa.String(length=512), nullable=True),
        sa.Column('available_to_speak', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('available_to_mentor', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('speaker_topics', sa.String(length=1000), nullable=True),
        sa.Column('mentoring_topics', sa.String(length=1000), nullable=True),
        sa.Column('preferred_engagement_types', sa.String(length=500), nullable=True),
        sa.Column('show_email', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('show_phone', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('show_birthday', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('show_location', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('show_current_employer', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('show_job_title', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('show_education', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('show_linkedin', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('show_social_links', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('show_work_history', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('show_education_history', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('alumni_id', sa.String(length=36), nullable=True),
        sa.Column('link_status', sa.String(length=32), nullable=False, server_default='unmatched'),
        sa.Column('link_confidence', sa.Integer(), nullable=True),
        sa.Column('linked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('linked_by', sa.String(length=64), nullable=True),
        sa.Column('match_evidence', sa.Text(), nullable=True),
        sa.Column('needs_review', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['alumni_id'], ['alumni.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', name='uq_user_profiles_user_id'),
    )
    op.create_index('ix_user_profiles_user_id', 'user_profiles', ['user_id'])
    op.create_index('ix_user_profiles_alumni_id', 'user_profiles', ['alumni_id'])
    op.create_index('ix_user_profiles_link_status', 'user_profiles', ['link_status'])

    op.create_table(
        'user_work_history',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('user_profile_id', sa.String(length=36), nullable=False),
        sa.Column('employer', sa.String(length=255), nullable=False),
        sa.Column('job_title', sa.String(length=255), nullable=True),
        sa.Column('start_date', sa.Date(), nullable=True),
        sa.Column('end_date', sa.Date(), nullable=True),
        sa.Column('is_current', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('display_order', sa.Integer(), nullable=False, server_default='0'),
        sa.ForeignKeyConstraint(['user_profile_id'], ['user_profiles.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_user_work_history_user_profile_id', 'user_work_history', ['user_profile_id'])

    op.create_table(
        'user_education_history',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('user_profile_id', sa.String(length=36), nullable=False),
        sa.Column('institution', sa.String(length=255), nullable=False),
        sa.Column('degree', sa.String(length=255), nullable=True),
        sa.Column('field_of_study', sa.String(length=255), nullable=True),
        sa.Column('start_year', sa.Integer(), nullable=True),
        sa.Column('graduation_year', sa.Integer(), nullable=True),
        sa.Column('is_current', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('display_order', sa.Integer(), nullable=False, server_default='0'),
        sa.ForeignKeyConstraint(['user_profile_id'], ['user_profiles.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_user_education_history_user_profile_id', 'user_education_history', ['user_profile_id'])

    op.create_table(
        'profile_match_candidates',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('user_profile_id', sa.String(length=36), nullable=False),
        sa.Column('alumni_id', sa.String(length=36), nullable=False),
        sa.Column('match_type', sa.String(length=16), nullable=False),
        sa.Column('score', sa.Integer(), nullable=False),
        sa.Column('matched_signals', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='candidate'),
        sa.ForeignKeyConstraint(['user_profile_id'], ['user_profiles.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['alumni_id'], ['alumni.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_profile_id', 'alumni_id', name='uq_profile_match_candidate'),
    )
    op.create_index('ix_profile_match_candidates_user_profile_id', 'profile_match_candidates', ['user_profile_id'])
    op.create_index('ix_profile_match_candidates_alumni_id', 'profile_match_candidates', ['alumni_id'])

    # Note: alumni.email and alumni.linkedin_url (the matcher's raw-value
    # lookup columns) are already indexed via `index=True` on the Alumni
    # model (ix_alumni_email, ix_alumni_linkedin_url) - not duplicated here.


def downgrade() -> None:
    op.drop_index('ix_profile_match_candidates_alumni_id', table_name='profile_match_candidates')
    op.drop_index('ix_profile_match_candidates_user_profile_id', table_name='profile_match_candidates')
    op.drop_table('profile_match_candidates')

    op.drop_index('ix_user_education_history_user_profile_id', table_name='user_education_history')
    op.drop_table('user_education_history')

    op.drop_index('ix_user_work_history_user_profile_id', table_name='user_work_history')
    op.drop_table('user_work_history')

    op.drop_index('ix_user_profiles_link_status', table_name='user_profiles')
    op.drop_index('ix_user_profiles_alumni_id', table_name='user_profiles')
    op.drop_index('ix_user_profiles_user_id', table_name='user_profiles')
    op.drop_table('user_profiles')
