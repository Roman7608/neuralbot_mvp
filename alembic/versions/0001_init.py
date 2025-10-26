from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001_init'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('departments',
        sa.Column('code', sa.String(), primary_key=True),
        sa.Column('name', sa.String(), nullable=True),
        sa.Column('city', sa.String(), nullable=True),
        sa.Column('brand', sa.String(), nullable=True),
        sa.Column('ext', sa.String(), nullable=True),
        sa.Column('sip_target', sa.String(), nullable=True),
        sa.Column('tg_chat_id', sa.String(), nullable=True),
        sa.Column('type', sa.Enum('sales','service','spares','other', name='departmenttype'), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
    )
    op.create_table('leads',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(), nullable=True),
        sa.Column('phone', sa.String(), nullable=False),
        sa.Column('city', sa.String(), nullable=True),
        sa.Column('brand', sa.String(), nullable=True),
        sa.Column('department_code', sa.String(), nullable=True),
        sa.Column('source', sa.Enum('phone','telegram','web', name='leadsource'), nullable=False),
        sa.Column('status', sa.Enum('new','in_progress','transferred','failed','done', name='leadstatus'), nullable=False, server_default='new'),
        sa.Column('comment', sa.String(), nullable=True),
        sa.Column('context_json', postgresql.JSONB(astext_type=sa.Text())),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_leads_phone', 'leads', ['phone'])
    op.create_index('ix_leads_department_code', 'leads', ['department_code'])
    op.create_table('call_logs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('lead_id', sa.Integer(), sa.ForeignKey('leads.id'), nullable=True),
        sa.Column('call_id', sa.String(), nullable=False),
        sa.Column('from_number', sa.String(), nullable=True),
        sa.Column('to_number', sa.String(), nullable=True),
        sa.Column('department_code', sa.String(), nullable=True),
        sa.Column('direction', sa.Enum('in','out', name='calldirection'), nullable=False),
        sa.Column('result', sa.Enum('answer','busy','noanswer','fail','hangup_by_user', name='callresult'), nullable=True),
        sa.Column('recording_url', sa.String(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('duration_sec', sa.Integer(), nullable=True),
    )
    op.create_table('working_hours',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('dept_code', sa.String(), sa.ForeignKey('departments.code'), nullable=True),
        sa.Column('weekday', sa.Integer(), nullable=False),
        sa.Column('start_time', sa.String(), nullable=False),
        sa.Column('end_time', sa.String(), nullable=False),
        sa.Column('timezone', sa.String(), nullable=False),
    )
    op.create_table('work_exceptions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('date', sa.String(), nullable=False),
        sa.Column('dept_code', sa.String(), sa.ForeignKey('departments.code'), nullable=True),
        sa.Column('is_closed', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('start_time', sa.String(), nullable=True),
        sa.Column('end_time', sa.String(), nullable=True),
        sa.Column('note', sa.String(), nullable=True),
    )
    op.create_table('llm_usage',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('provider', sa.String(), nullable=True),
        sa.Column('model', sa.String(), nullable=True),
        sa.Column('prompt_tokens', sa.Integer(), nullable=True),
        sa.Column('completion_tokens', sa.Integer(), nullable=True),
        sa.Column('cost_minor_units', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
    )
    op.create_table('audit_logs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('actor', sa.String(), nullable=True),
        sa.Column('channel', sa.String(), nullable=True),
        sa.Column('event', sa.String(), nullable=True),
        sa.Column('payload_json', postgresql.JSONB(astext_type=sa.Text())),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
    )

def downgrade():
    op.drop_table('audit_logs')
    op.drop_table('llm_usage')
    op.drop_table('work_exceptions')
    op.drop_table('working_hours')
    op.drop_table('call_logs')
    op.drop_index('ix_leads_department_code', table_name='leads')
    op.drop_index('ix_leads_phone', table_name='leads')
    op.drop_table('leads')
    op.drop_table('departments')
    op.execute("DROP TYPE IF EXISTS departmenttype")
    op.execute("DROP TYPE IF EXISTS leadsource")
    op.execute("DROP TYPE IF EXISTS leadstatus")
    op.execute("DROP TYPE IF EXISTS calldirection")
    op.execute("DROP TYPE IF EXISTS callresult")

















