"""Initial schema for Auth Service

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
        'users',
        sa.Column('user_id', sa.String(), primary_key=True),
        sa.Column('username', sa.String(), nullable=False, unique=True),
        sa.Column('password_hash', sa.String(), nullable=False),
        sa.Column('salt', sa.String(), nullable=False),
        sa.Column('role', sa.String(), nullable=False),
        sa.Column('is_active', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.Column('updated_at', sa.String(), nullable=False),
        if_not_exists=True,
    )
    op.create_index('idx_users_username', 'users', ['username'], if_not_exists=True)

    op.create_table(
        'refresh_tokens',
        sa.Column('token_id', sa.String(), primary_key=True),
        sa.Column('user_id', sa.String(), sa.ForeignKey('users.user_id'), nullable=False),
        sa.Column('token_hash', sa.String(), nullable=False),
        sa.Column('expires_at', sa.String(), nullable=False),
        sa.Column('revoked_at', sa.String(), nullable=True),
        sa.Column('created_at', sa.String(), nullable=False),
        if_not_exists=True,
    )
    op.create_index('idx_tokens_hash', 'refresh_tokens', ['token_hash'], if_not_exists=True)
    op.create_index('idx_tokens_user', 'refresh_tokens', ['user_id'], if_not_exists=True)

    op.create_table(
        'ws_tickets',
        sa.Column('ticket_id', sa.String(), primary_key=True),
        sa.Column('ticket_hash', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), sa.ForeignKey('users.user_id'), nullable=False),
        sa.Column('role', sa.String(), nullable=False),
        sa.Column('expires_at', sa.String(), nullable=False),
        sa.Column('used_at', sa.String(), nullable=True),
        sa.Column('created_at', sa.String(), nullable=False),
        if_not_exists=True,
    )
    op.create_index('idx_ws_ticket_hash', 'ws_tickets', ['ticket_hash'], if_not_exists=True)
    op.create_index('idx_tickets_user', 'ws_tickets', ['user_id'], if_not_exists=True)



def downgrade() -> None:

    op.drop_index('idx_tickets_user', table_name='ws_tickets', if_exists=True)
    op.drop_index('idx_ws_ticket_hash', table_name='ws_tickets', if_exists=True)
    op.drop_table('ws_tickets', if_exists=True)
    op.drop_index('idx_tokens_user', table_name='refresh_tokens', if_exists=True)
    op.drop_index('idx_tokens_hash', table_name='refresh_tokens', if_exists=True)
    op.drop_table('refresh_tokens', if_exists=True)
    op.drop_index('idx_users_username', table_name='users', if_exists=True)
    op.drop_table('users', if_exists=True)

    op.drop_table('processed_events', if_exists=True)
    op.drop_index('idx_outbox_published', table_name='outbox_events', if_exists=True)
    op.drop_table('outbox_events', if_exists=True)

