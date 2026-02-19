from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from enum import Enum


class LeadStatus(str, Enum):
    new = "new"
    contacted = "contacted"
    qualified = "qualified"
    converted = "converted"
    discarded = "discarded"


class LeadInput(BaseModel):
    full_name: str = Field(..., min_length=2)
    email: EmailStr
    phone: str = Field(..., min_length=6)
    source: str = Field(..., description="meta_ads, google, organic, referral, etc.")
    notes: Optional[str] = ""


class Lead(BaseModel):
    id: str
    full_name: str
    email: str
    phone: str
    source: str
    notes: str
    status: LeadStatus
    created_at: str
