from pydantic import BaseModel, EmailStr, Field
from typing import Optional, Literal
from enum import Enum


class InfluencerChannel(str, Enum):
    ignacia_rios = "ignacia_rios"
    max_andrade = "max_andrade"


class LeadTier(str, Enum):
    premium = "premium"
    standard = "standard"


# --- Ignacia Rios ---

class IncomeRange(str, Enum):
    bajo = "bajo"           # < $500k CLP
    medio = "medio"         # $500k-$1.5M CLP
    alto = "alto"           # > $1.5M CLP


class IgnaciaLeadInput(BaseModel):
    full_name: str = Field(..., min_length=2)
    email: EmailStr
    phone: str = Field(..., min_length=8)
    monthly_income_range: IncomeRange
    applying_for_credit_soon: bool  # planning credit in next 3 months
    has_existing_debt: bool
    interested_in_investments: bool


# --- Max Andrade ---

class BusinessStage(str, Enum):
    idea = "idea"
    starting = "starting"       # < 3 meses
    growing = "growing"         # 3-12 meses con ventas
    scaling = "scaling"         # > 12 meses y revenue estable


class RevenueRange(str, Enum):
    none = "none"
    low = "low"         # < $500 USD/mes
    mid = "mid"         # $500-$2000 USD/mes
    high = "high"       # > $2000 USD/mes


class MaxLeadInput(BaseModel):
    full_name: str = Field(..., min_length=2)
    email: EmailStr
    whatsapp: str = Field(..., min_length=8)
    business_stage: BusinessStage
    monthly_revenue_range: RevenueRange
    has_defined_niche: bool
    ready_to_invest: bool


# --- Lead output ---

class LeadScore(BaseModel):
    total: int
    breakdown: dict[str, int]
    tier: LeadTier


class Lead(BaseModel):
    id: str
    channel: InfluencerChannel
    full_name: str
    email: str
    contact: str  # phone (Ignacia) or whatsapp (Max)
    score: LeadScore
    raw_data: dict
    created_at: str
