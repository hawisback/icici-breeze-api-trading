"""Initial schema for Broker Session Service

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
        'broker_accounts',
        sa.Column('account_id', sa.String(), primary_key=True),
        sa.Column('broker_name', sa.String(), nullable=False),
        sa.Column('account_name', sa.String(), nullable=False),
        sa.Column('api_key', sa.String(), nullable=False),
        sa.Column('created_at', sa.String(), nullable=False),
        if_not_exists=True,
    )
    op.create_table(
        'session_history',
        sa.Column('session_id', sa.String(), primary_key=True),
        sa.Column('account_id', sa.String(), sa.ForeignKey('broker_accounts.account_id'), nullable=False),
        sa.Column('session_token_masked', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('login_time', sa.String(), nullable=False),
        sa.Column('expires_at', sa.String(), nullable=False),
        sa.Column('metadata', sa.Text(), nullable=True),
        if_not_exists=True,
    )
    op.create_table(
        'health_history',
        sa.Column('check_id', sa.String(), primary_key=True),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('latency_ms', sa.Float(), nullable=False),
        sa.Column('message', sa.String(), nullable=True),
        sa.Column('checked_at', sa.String(), nullable=False),
        if_not_exists=True,
    )



def downgrade() -> None:

    op.drop_table('health_history', if_exists=True)
    op.drop_table('session_history', if_exists=True)
    op.drop_table('broker_accounts', if_exists=True)

    op.drop_table('processed_events', if_exists=True)
    op.drop_index('idx_outbox_published', table_name='outbox_events', if_exists=True)
    op.drop_table('outbox_events', if_exists=True)

