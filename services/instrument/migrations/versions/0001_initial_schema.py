"""Initial schema for Instrument Master Service

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
        'instruments',
        sa.Column('instrument_id', sa.String(), primary_key=True),
        sa.Column('broker', sa.String(), nullable=False, server_default='ICICI_BREEZE'),
        sa.Column('exchange', sa.String(), nullable=False),
        sa.Column('segment', sa.String(), nullable=False),
        sa.Column('underlying', sa.String(), nullable=False),
        sa.Column('stock_code', sa.String(), nullable=False),
        sa.Column('expiry', sa.String(), nullable=True),
        sa.Column('strike', sa.Float(), nullable=True),
        sa.Column('option_right', sa.String(), nullable=True),
        sa.Column('lot_size', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('tick_size', sa.Float(), nullable=False, server_default='0.05'),
        sa.Column('broker_token', sa.String(), nullable=True),
        sa.Column('tradable', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('valid_from', sa.String(), nullable=True),
        sa.Column('valid_to', sa.String(), nullable=True),
        if_not_exists=True,
    )
    op.create_index(
        'idx_inst_lookup',
        'instruments',
        ['underlying', 'expiry', 'strike', 'option_right'],
        if_not_exists=True,
    )
    op.create_index('idx_inst_stock', 'instruments', ['stock_code'], if_not_exists=True)



def downgrade() -> None:

    op.drop_index('idx_inst_stock', table_name='instruments', if_exists=True)
    op.drop_index('idx_inst_lookup', table_name='instruments', if_exists=True)
    op.drop_table('instruments', if_exists=True)

    op.drop_table('processed_events', if_exists=True)
    op.drop_index('idx_outbox_published', table_name='outbox_events', if_exists=True)
    op.drop_table('outbox_events', if_exists=True)

