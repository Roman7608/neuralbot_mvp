from pydantic import BaseModel, Field
from typing import Optional
class LeadCreate(BaseModel):
    name: Optional[str] = None
    phone: str = Field(..., min_length=10, max_length=20)
    city: Optional[str] = None
    brand: Optional[str] = None
    department_code: Optional[str] = None
    source: str = Field(..., pattern="^(phone|telegram|web)$")
    comment: Optional[str] = None
class LeadOut(BaseModel):
    id: int
    class Config: orm_mode = True
