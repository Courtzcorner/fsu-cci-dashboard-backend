"""event speaker requests

Revision ID: d4e5f6a7b8c9
Revises: 90745d7d8acb
Create Date: 2026-08-06 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = '90745d7d8acb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'event_speaker_requests',
        sa.Column('organization_id', sa.String(length=36), nullable=False),
        sa.Column('event_id', sa.String(length=36), nullable=False),
        sa.Column('alumni_id', sa.String(length=36), nullable=False),
        sa.Column('message', sa.String(length=2000), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='requested'),
        sa.Column('selected_by_user_id', sa.String(length=36), nullable=True),
        sa.Column('selected_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['alumni_id'], ['alumni.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['selected_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_id', 'alumni_id', name='uq_speaker_request_event_alumni'),
    )
    op.create_index(
        op.f('ix_event_speaker_requests_organization_id'), 'event_speaker_requests', ['organization_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_event_speaker_requests_event_id'), 'event_speaker_requests', ['event_id'], unique=False,
    )
    op.create_index(
        op.f('ix_event_speaker_requests_alumni_id'), 'event_speaker_requests', ['alumni_id'], unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_event_speaker_requests_alumni_id'), table_name='event_speaker_requests')
    op.drop_index(op.f('ix_event_speaker_requests_event_id'), table_name='event_speaker_requests')
    op.drop_index(op.f('ix_event_speaker_requests_organization_id'), table_name='event_speaker_requests')
    op.drop_table('event_speaker_requests')
