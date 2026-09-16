"""Initial schema for Order Management System

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
        'order_intents',
        sa.Column('intent_id', sa.String(), primary_key=True),
        sa.Column('correlation_id', sa.String(), nullable=False),
        sa.Column('strategy_instance_id', sa.String(), nullable=True),
        sa.Column('source', sa.String(), nullable=False),
        sa.Column('instrument_id', sa.String(), nullable=False),
        sa.Column('symbol', sa.String(), nullable=False),
        sa.Column('side', sa.String(), nullable=False),
        sa.Column('order_type', sa.String(), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('price', sa.Float(), nullable=False),
        sa.Column('trigger_price', sa.Float(), nullable=True),
        sa.Column('product', sa.String(), nullable=False),
        sa.Column('time_in_force', sa.String(), nullable=False),
        sa.Column('trading_mode', sa.String(), nullable=False),
        sa.Column('created_at', sa.String(), nullable=False),
        if_not_exists=True,
    )
    op.create_index('idx_order_intents_time', 'order_intents', ['created_at'], if_not_exists=True)

    op.create_table(
        'broker_orders',
        sa.Column('order_id', sa.String(), primary_key=True),
        sa.Column('intent_id', sa.String(), sa.ForeignKey('order_intents.intent_id'), nullable=False),
        sa.Column('client_order_id', sa.String(), nullable=False, unique=True),
        sa.Column('broker_order_id', sa.String(), nullable=True),
        sa.Column('instrument_id', sa.String(), nullable=False),
        sa.Column('symbol', sa.String(), nullable=False),
        sa.Column('side', sa.String(), nullable=False),
        sa.Column('order_type', sa.String(), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('filled_quantity', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('remaining_quantity', sa.Integer(), nullable=False),
        sa.Column('price', sa.Float(), nullable=False),
        sa.Column('average_price', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('status_message', sa.String(), nullable=True),
        sa.Column('trading_mode', sa.String(), nullable=False),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.Column('updated_at', sa.String(), nullable=False),
        if_not_exists=True,
    )
    op.create_index('idx_orders_status', 'broker_orders', ['status'], if_not_exists=True)
    op.create_index('idx_orders_client_id', 'broker_orders', ['client_order_id'], if_not_exists=True)
    op.create_index('idx_orders_broker_id', 'broker_orders', ['broker_order_id'], if_not_exists=True)

    op.create_table(
        'order_events',
        sa.Column('event_id', sa.String(), primary_key=True),
        sa.Column('order_id', sa.String(), sa.ForeignKey('broker_orders.order_id'), nullable=False),
        sa.Column('from_state', sa.String(), nullable=True),
        sa.Column('to_state', sa.String(), nullable=False),
        sa.Column('reason', sa.String(), nullable=True),
        sa.Column('payload', sa.Text(), nullable=True),
        sa.Column('occurred_at', sa.String(), nullable=False),
        if_not_exists=True,
    )
    op.create_index('idx_order_events_time', 'order_events', ['order_id', 'occurred_at'], if_not_exists=True)



def downgrade() -> None:

    op.drop_index('idx_order_events_time', table_name='order_events', if_exists=True)
    op.drop_table('order_events', if_exists=True)
    op.drop_index('idx_orders_broker_id', table_name='broker_orders', if_exists=True)
    op.drop_index('idx_orders_client_id', table_name='broker_orders', if_exists=True)
    op.drop_index('idx_orders_status', table_name='broker_orders', if_exists=True)
    op.drop_table('broker_orders', if_exists=True)
    op.drop_index('idx_order_intents_time', table_name='order_intents', if_exists=True)
    op.drop_table('order_intents', if_exists=True)

    op.drop_table('processed_events', if_exists=True)
    op.drop_index('idx_outbox_published', table_name='outbox_events', if_exists=True)
    op.drop_table('outbox_events', if_exists=True)

