"""Initial schema for Audit Service

Revision ID: 0001
Revises: None
Create Date: 2026-09-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Core Transactional Outbox
    op.create_table(
        'outbox_events',
        sa.Column('event_id', sa.String(), primary_key=True),
        sa.Column('topic', sa.String(), nullable=False),
        sa.Column('payload', sa.Text(), nullable=False),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.Column('published', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('published_at', sa.String(), nullable=True),
        if_not_exists=True,
    )
    op.create_index(
        'idx_outbox_published',
        'outbox_events',
        ['published', 'created_at'],
        if_not_exists=True,
    )

    # Core Idempotent Inbox Deduplication
    op.create_table(
        'processed_events',
        sa.Column('event_id', sa.String(), nullable=False),
        sa.Column('consumer_name', sa.String(), nullable=False),
        sa.Column('processed_at', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('event_id', 'consumer_name'),
        if_not_exists=True,
    )


    op.create_table(
        'audit_events',
        sa.Column('event_id', sa.String(), primary_key=True),
        sa.Column('event_type', sa.String(), nullable=False),
        sa.Column('correlation_id', sa.String(), nullable=False),
        sa.Column('source', sa.String(), nullable=False),
        sa.Column('payload', sa.Text(), nullable=False),
        sa.Column('occurred_at', sa.String(), nullable=False),
        if_not_exists=True,
    )
    op.create_index('idx_audit_time', 'audit_events', ['occurred_at'], if_not_exists=True)
    op.create_index('idx_audit_corr', 'audit_events', ['correlation_id'], if_not_exists=True)



def downgrade() -> None:

    op.drop_index('idx_audit_corr', table_name='audit_events', if_exists=True)
    op.drop_index('idx_audit_time', table_name='audit_events', if_exists=True)
    op.drop_table('audit_events', if_exists=True)

    op.drop_table('processed_events', if_exists=True)
    op.drop_index('idx_outbox_published', table_name='outbox_events', if_exists=True)
    op.drop_table('outbox_events', if_exists=True)

