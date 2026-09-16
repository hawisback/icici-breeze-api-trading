"""Initial schema for Portfolio Service

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
        'executions',
        sa.Column('execution_id', sa.String(), primary_key=True),
        sa.Column('order_id', sa.String(), nullable=False),
        sa.Column('broker_execution_id', sa.String(), nullable=True),
        sa.Column('instrument_id', sa.String(), nullable=False),
        sa.Column('symbol', sa.String(), nullable=False),
        sa.Column('side', sa.String(), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('price', sa.Float(), nullable=False),
        sa.Column('fee', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('execution_time', sa.String(), nullable=False),
        if_not_exists=True,
    )
    op.create_index('idx_exec_instrument', 'executions', ['instrument_id'], if_not_exists=True)

    op.create_table(
        'positions',
        sa.Column('position_id', sa.String(), primary_key=True),
        sa.Column('instrument_id', sa.String(), nullable=False, unique=True),
        sa.Column('symbol', sa.String(), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('buy_quantity', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('sell_quantity', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('buy_value', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('sell_value', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('average_price', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('current_price', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('realized_pnl', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('unrealized_pnl', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('total_pnl', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('trading_mode', sa.String(), nullable=False),
        sa.Column('updated_at', sa.String(), nullable=False),
        if_not_exists=True,
    )
    op.create_index('idx_pos_instrument', 'positions', ['instrument_id'], if_not_exists=True)

    op.create_table(
        'pnl_snapshots',
        sa.Column('snapshot_id', sa.String(), primary_key=True),
        sa.Column('realized_pnl', sa.Float(), nullable=False),
        sa.Column('unrealized_pnl', sa.Float(), nullable=False),
        sa.Column('total_pnl', sa.Float(), nullable=False),
        sa.Column('day_pnl', sa.Float(), nullable=False),
        sa.Column('open_positions_count', sa.Integer(), nullable=False),
        sa.Column('timestamp', sa.String(), nullable=False),
        if_not_exists=True,
    )



def downgrade() -> None:

    op.drop_table('pnl_snapshots', if_exists=True)
    op.drop_index('idx_pos_instrument', table_name='positions', if_exists=True)
    op.drop_table('positions', if_exists=True)
    op.drop_index('idx_exec_instrument', table_name='executions', if_exists=True)
    op.drop_table('executions', if_exists=True)

    op.drop_table('processed_events', if_exists=True)
    op.drop_index('idx_outbox_published', table_name='outbox_events', if_exists=True)
    op.drop_table('outbox_events', if_exists=True)

