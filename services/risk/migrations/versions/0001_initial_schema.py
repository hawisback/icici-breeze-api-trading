"""Initial schema for Risk Management Service

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
        'system_modes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('mode', sa.String(), nullable=False, server_default='NORMAL'),
        sa.Column('updated_at', sa.String(), nullable=False),
        sa.CheckConstraint('id = 1', name='ck_system_modes_single_row'),
        if_not_exists=True,
    )
    op.create_table(
        'kill_switch_events',
        sa.Column('event_id', sa.String(), primary_key=True),
        sa.Column('action', sa.String(), nullable=False),
        sa.Column('reason', sa.String(), nullable=True),
        sa.Column('activated_by', sa.String(), nullable=False),
        sa.Column('activated_at', sa.String(), nullable=False),
        if_not_exists=True,
    )
    op.create_table(
        'risk_decisions',
        sa.Column('decision_id', sa.String(), primary_key=True),
        sa.Column('intent_id', sa.String(), nullable=False),
        sa.Column('approved', sa.Integer(), nullable=False),
        sa.Column('rule_name', sa.String(), nullable=True),
        sa.Column('reason', sa.String(), nullable=True),
        sa.Column('system_mode', sa.String(), nullable=False),
        sa.Column('evaluated_at', sa.String(), nullable=False),
        if_not_exists=True,
    )
    op.create_index('idx_risk_intent', 'risk_decisions', ['intent_id'], if_not_exists=True)

    # Seed baseline system mode if not present
    op.execute(
        "INSERT OR IGNORE INTO system_modes (id, mode, updated_at) "
        "VALUES (1, 'NORMAL', datetime('now'))"
    )



def downgrade() -> None:

    op.drop_index('idx_risk_intent', table_name='risk_decisions', if_exists=True)
    op.drop_table('risk_decisions', if_exists=True)
    op.drop_table('kill_switch_events', if_exists=True)
    op.drop_table('system_modes', if_exists=True)

    op.drop_table('processed_events', if_exists=True)
    op.drop_index('idx_outbox_published', table_name='outbox_events', if_exists=True)
    op.drop_table('outbox_events', if_exists=True)

