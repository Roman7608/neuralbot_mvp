from sqlalchemy import Column, Integer, String, DateTime, Enum, Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func
from .db import Base
import enum
class LeadSource(str, enum.Enum): phone="phone"; telegram="telegram"; web="web"; voice_bot="voice_bot"
class LeadStatus(str, enum.Enum): new="new"; in_progress="in_progress"; transferred="transferred"; failed="failed"; done="done"
class Lead(Base):
    __tablename__="leads"
    id=Column(Integer, primary_key=True)
    name=Column(String); phone=Column(String, index=True, nullable=False)
    city=Column(String); brand=Column(String); department_code=Column(String, index=True)
    source=Column(Enum(LeadSource), nullable=False)
    status=Column(Enum(LeadStatus), default=LeadStatus.new, nullable=False)
    comment=Column(String); context_json=Column(JSONB)
    created_at=Column(DateTime(timezone=True), server_default=func.now())
    updated_at=Column(DateTime(timezone=True), onupdate=func.now())

class CallDirection(str, enum.Enum): inbound="in"; outbound="out"
class CallResult(str, enum.Enum): answer="answer"; busy="busy"; noanswer="noanswer"; fail="fail"; hangup_by_user="hangup_by_user"
class CallLog(Base):
    __tablename__="call_logs"
    id=Column(Integer, primary_key=True)
    lead_id=Column(Integer, ForeignKey("leads.id"), nullable=True)
    call_id=Column(String, index=True, nullable=True)
    caller_id=Column(String)  # Номер звонящего
    channel=Column(String)  # Канал Asterisk
    from_number=Column(String); to_number=Column(String)
    department_code=Column(String, index=True)
    direction=Column(String, nullable=False)  # inbound/outbound
    status=Column(String)  # ringing/answered/completed/etc
    result=Column(Enum(CallResult), nullable=True)
    recording_url=Column(String)
    routed_to=Column(String)  # Куда переадресован
    routed_at=Column(DateTime(timezone=True))  # Когда переадресован
    hangup_cause=Column(String)  # Причина завершения
    started_at=Column(DateTime(timezone=True))
    ended_at=Column(DateTime(timezone=True))
    duration_sec=Column(Integer)

class DepartmentType(str, enum.Enum): sales="sales"; service="service"; spares="spares"; other="other"
class Department(Base):
    __tablename__="departments"
    code=Column(String, primary_key=True)
    name=Column(String)
    city=Column(String)
    brand=Column(String, nullable=True)
    ext=Column(String)
    sip_target=Column(String)
    tg_chat_id=Column(String, nullable=True)
    type=Column(Enum(DepartmentType), nullable=False)
    enabled=Column(Boolean, default=True, nullable=False)

class WorkingHour(Base):
    __tablename__="working_hours"
    id=Column(Integer, primary_key=True)
    dept_code=Column(String, ForeignKey("departments.code"), nullable=True)
    weekday=Column(Integer, nullable=False)  # 0-6
    start_time=Column(String, nullable=False)  # HH:MM
    end_time=Column(String, nullable=False)    # HH:MM
    timezone=Column(String, nullable=False, default="Europe/Samara")

class WorkException(Base):
    __tablename__="work_exceptions"
    id=Column(Integer, primary_key=True)
    date=Column(String, nullable=False)  # YYYY-MM-DD
    dept_code=Column(String, ForeignKey("departments.code"), nullable=True)
    is_closed=Column(Boolean, default=False, nullable=False)
    start_time=Column(String, nullable=True)
    end_time=Column(String, nullable=True)
    note=Column(String, nullable=True)

class LlmUsage(Base):
    __tablename__="llm_usage"
    id=Column(Integer, primary_key=True)
    provider=Column(String)
    model=Column(String)
    prompt_tokens=Column(Integer)
    completion_tokens=Column(Integer)
    cost_minor_units=Column(Integer)
    created_at=Column(DateTime(timezone=True), server_default=func.now())

class AuditLog(Base):
    __tablename__="audit_logs"
    id=Column(Integer, primary_key=True)
    actor=Column(String)  # system|user|bot
    channel=Column(String)  # phone|telegram|web|system
    event=Column(String)
    payload_json=Column(JSONB)
    created_at=Column(DateTime(timezone=True), server_default=func.now())
