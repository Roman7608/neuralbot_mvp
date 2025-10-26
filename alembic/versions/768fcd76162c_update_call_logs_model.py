"""update_call_logs_model

Revision ID: 768fcd76162c
Revises: 0001_init
Create Date: 2025-10-08 15:59:26.527129

"""
from alembic import op
import sqlalchemy as sa

revision = '768fcd76162c'
down_revision = '0001_init'
branch_labels = None
depends_on = None

def upgrade():
    # Добавляем новые поля в call_logs
    op.add_column('call_logs', sa.Column('caller_id', sa.String(), nullable=True))
    op.add_column('call_logs', sa.Column('channel', sa.String(), nullable=True))
    op.add_column('call_logs', sa.Column('status', sa.String(), nullable=True))
    op.add_column('call_logs', sa.Column('routed_to', sa.String(), nullable=True))
    op.add_column('call_logs', sa.Column('routed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('call_logs', sa.Column('hangup_cause', sa.String(), nullable=True))
    
    # Изменяем тип direction с enum на string
    op.execute("ALTER TABLE call_logs ALTER COLUMN direction TYPE VARCHAR USING direction::text")
    
    # Делаем call_id nullable
    op.alter_column('call_logs', 'call_id', nullable=True)

def downgrade():
    # Удаляем добавленные поля
    op.drop_column('call_logs', 'hangup_cause')
    op.drop_column('call_logs', 'routed_at')
    op.drop_column('call_logs', 'routed_to')
    op.drop_column('call_logs', 'status')
    op.drop_column('call_logs', 'channel')
    op.drop_column('call_logs', 'caller_id')
    
    # Возвращаем enum для direction
    op.execute("ALTER TABLE call_logs ALTER COLUMN direction TYPE calldirection USING direction::calldirection")
    
    # Делаем call_id NOT NULL
    op.alter_column('call_logs', 'call_id', nullable=False)



