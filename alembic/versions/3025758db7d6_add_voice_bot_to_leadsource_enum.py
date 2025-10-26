"""Add voice_bot to LeadSource enum

Revision ID: 3025758db7d6
Revises: 768fcd76162c
Create Date: 2025-10-20 20:38:21.946638

"""
from alembic import op
import sqlalchemy as sa

revision = '3025758db7d6'
down_revision = '768fcd76162c'
branch_labels = None
depends_on = None

def upgrade():
    # Добавляем новое значение в enum leadsource
    op.execute("ALTER TYPE leadsource ADD VALUE 'voice_bot'")

def downgrade():
    # PostgreSQL не поддерживает удаление значений из enum
    # Это требует пересоздания enum
    pass


















