"""Initial schema for Broker Gateway Service

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

    # Broker Write Requests Ledger (durable idempotency & unknown recovery)
    op.create_table(
        'broker_write_requests',
        sa.Column('request_id', sa.String(), primary_key=True),
        sa.Column('account_id', sa.String(), nullable=False),
        sa.Column('client_reference', sa.String(), nullable=False),
        sa.Column('request_hash', sa.String(), nullable=False),
        sa.Column('action', sa.String(), nullable=False),
        sa.Column('state', sa.String(), nullable=False),
        sa.Column('broker_order_id', sa.String(), nullable=True),
        sa.Column('payload_json', sa.Text(), nullable=False),
        sa.Column('result_json', sa.Text(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.Column('updated_at', sa.String(), nullable=False),
        if_not_exists=True,
    )
    op.create_index('idx_bwr_client_ref', 'broker_write_requests', ['client_reference'], if_not_exists=True)
    op.create_index('idx_bwr_broker_order', 'broker_write_requests', ['broker_order_id'], if_not_exists=True)
    op.create_index('idx_bwr_state', 'broker_write_requests', ['state'], if_not_exists=True)

    # Broker Call Audit Trail
    op.create_table(
        'broker_call_audit',
        sa.Column('call_id', sa.String(), primary_key=True),
        sa.Column('endpoint', sa.String(), nullable=False),
        sa.Column('method', sa.String(), nullable=False),
        sa.Column('duration_ms', sa.Float(), nullable=False),
        sa.Column('status_code', sa.Integer(), nullable=True),
        sa.Column('error_type', sa.String(), nullable=True),
        sa.Column('occurred_at', sa.String(), nullable=False),
        if_not_exists=True,
    )
    op.create_index('idx_bca_time', 'broker_call_audit', ['occurred_at'], if_not_exists=True)

    # Session Runtime Metadata
    op.create_table(
        'session_runtime_metadata',
        sa.Column('session_id', sa.String(), primary_key=True),
        sa.Column('account_id', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('login_time', sa.String(), nullable=False),
        sa.Column('expires_at', sa.String(), nullable=False),
        sa.Column('last_health_check', sa.String(), nullable=True),
        sa.Column('metadata_json', sa.Text(), nullable=True),
        if_not_exists=True,
    )

    # Market & Order Notification Subscription Registry
    op.create_table(
        'subscription_registry',
        sa.Column('subscription_id', sa.String(), primary_key=True),
        sa.Column('symbol', sa.String(), nullable=False),
        sa.Column('exchange', sa.String(), nullable=False),
        sa.Column('feed_type', sa.String(), nullable=False),
        sa.Column('interval', sa.String(), nullable=True),
        sa.Column('subscribed_at', sa.String(), nullable=False),
        if_not_exists=True,
    )
    op.create_index(
        'idx_sub_sym',
        'subscription_registry',
        ['symbol', 'exchange', 'feed_type'],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index('idx_sub_sym', table_name='subscription_registry', if_exists=True)
    op.drop_table('subscription_registry', if_exists=True)
    op.drop_table('session_runtime_metadata', if_exists=True)
    op.drop_index('idx_bca_time', table_name='broker_call_audit', if_exists=True)
    op.drop_table('broker_call_audit', if_exists=True)
    op.drop_index('idx_bwr_state', table_name='broker_write_requests', if_exists=True)
    op.drop_index('idx_bwr_broker_order', table_name='broker_write_requests', if_exists=True)
    op.drop_index('idx_bwr_client_ref', table_name='broker_write_requests', if_exists=True)
    op.drop_table('broker_write_requests', if_exists=True)
    op.drop_table('processed_events', if_exists=True)
    op.drop_index('idx_outbox_published', table_name='outbox_events', if_exists=True)
    op.drop_table('outbox_events', if_exists=True)

