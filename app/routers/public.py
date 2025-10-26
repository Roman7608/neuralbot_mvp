from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..db import SessionLocal
from ..schemas import LeadCreate, LeadOut
from ..models import Lead, LeadSource
from ..services.leads import get_departments_config
from ..services.telegram import notify_department_about_lead
router = APIRouter()
def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()
@router.post("/leads", response_model=LeadOut, status_code=201)
async def create_lead(payload: LeadCreate, db: Session = Depends(get_db)):
    lead = Lead(
        name=payload.name,
        phone=payload.phone,
        city=payload.city,
        brand=payload.brand,
        department_code=payload.department_code,
        source=LeadSource(payload.source),
        comment=payload.comment,
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)
    
    # Уведомляем отдел в Telegram (если настроен)
    try:
        await notify_department_about_lead(lead)
    except Exception as e:
        print(f"Ошибка уведомления в Telegram: {e}")
    
    return lead
@router.get("/departments")
def departments_list(): return get_departments_config()
