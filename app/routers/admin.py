from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional
from ..db import SessionLocal
from ..models import Lead, CallLog, WorkingHour, WorkException, AuditLog

router = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/leads")
def admin_leads(db: Session = Depends(get_db)):
    return db.query(Lead).order_by(Lead.id.desc()).limit(200).all()

@router.patch("/leads/{lead_id}")
def admin_update_lead(lead_id: int, payload: dict, db: Session = Depends(get_db)):
    lead = db.query(Lead).filter(Lead.id==lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    for key, value in payload.items():
        if hasattr(lead, key):
            setattr(lead, key, value)
    
    db.commit()
    return {"ok": True}

@router.get("/call_logs")
def admin_call_logs(db: Session = Depends(get_db)):
    """Получение логов звонков"""
    return db.query(CallLog).order_by(CallLog.id.desc()).limit(200).all()

@router.get("/call_logs/{call_id}")
def admin_call_log_detail(call_id: int, db: Session = Depends(get_db)):
    """Детали конкретного звонка"""
    call_log = db.query(CallLog).filter(CallLog.id == call_id).first()
    if not call_log:
        raise HTTPException(status_code=404, detail="Call log not found")
    return call_log

@router.get("/call_stats")
def admin_call_stats(db: Session = Depends(get_db)):
    """Статистика звонков"""
    from sqlalchemy import func
    from datetime import datetime, timedelta
    
    # Общая статистика
    total_calls = db.query(CallLog).count()
    today_calls = db.query(CallLog).filter(
        CallLog.started_at >= datetime.now().date()
    ).count()
    
    # Статистика по статусам
    status_stats = db.query(
        CallLog.status, 
        func.count(CallLog.id)
    ).group_by(CallLog.status).all()
    
    # Статистика по направлениям
    direction_stats = db.query(
        CallLog.direction,
        func.count(CallLog.id)
    ).group_by(CallLog.direction).all()
    
    # Статистика за последние 7 дней
    week_ago = datetime.now() - timedelta(days=7)
    week_stats = db.query(
        func.date(CallLog.started_at).label('date'),
        func.count(CallLog.id).label('count')
    ).filter(
        CallLog.started_at >= week_ago
    ).group_by(
        func.date(CallLog.started_at)
    ).all()
    
    return {
        "total_calls": total_calls,
        "today_calls": today_calls,
        "status_stats": dict(status_stats),
        "direction_stats": dict(direction_stats),
        "week_stats": [{"date": str(stat.date), "count": stat.count} for stat in week_stats]
    }

@router.get("/work_schedule")
def get_work_schedule(db: Session = Depends(get_db)):
    return {
        "working_hours": db.query(WorkingHour).all(),
        "work_exceptions": db.query(WorkException).all(),
    }

@router.post("/work_schedule")
def set_work_schedule(body: dict, db: Session = Depends(get_db)):
    # naive replace strategy for MVP
    db.query(WorkingHour).delete()
    db.query(WorkException).delete()
    for wh in body.get("working_hours", []):
        db.add(WorkingHour(**wh))
    for we in body.get("work_exceptions", []):
        db.add(WorkException(**we))
    db.commit()
    return {"ok": True}

@router.get("/logs")
def admin_logs(db: Session = Depends(get_db)):
    return db.query(AuditLog).order_by(AuditLog.id.desc()).limit(200).all()

@router.get("/metrics")
def admin_metrics(db: Session = Depends(get_db)):
    leads_count = db.query(Lead).count()
    calls_count = db.query(CallLog).count()
    return {"leads": leads_count, "calls": calls_count}



