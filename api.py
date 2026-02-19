"""
Baku Agency - Leads Engine API

Endpoints:
  POST /leads/ignacia   → Capture lead from Ignacia Rios funnel
  POST /leads/max       → Capture lead from Max Andrade funnel
  GET  /leads           → List leads (filter by channel / tier)
  GET  /leads/{id}      → Get single lead
  GET  /stats           → Lead counts per channel and tier
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Query

from leads_engine.models import (
    IgnaciaLeadInput,
    MaxLeadInput,
    Lead,
    InfluencerChannel,
    LeadTier,
)
from leads_engine.scoring import score_ignacia, score_max
from leads_engine.storage import save_lead, get_leads, get_lead_by_id, count_by_tier

app = FastAPI(
    title="Baku Agency — Leads Engine",
    description=(
        "Captures, scores and segments leads from Ignacia Rios and Max Andrade funnels. "
        "Leads are automatically classified as Premium (score ≥ 70) or Standard."
    ),
    version="1.0.0",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@app.post("/leads/ignacia", response_model=Lead, status_code=201, tags=["Capture"])
def capture_ignacia(data: IgnaciaLeadInput):
    """
    Register a lead from the Ignacia Rios funnel (financial niche).

    Scoring criteria:
    - High income (> $1.5M CLP)  → 30 pts
    - Applying for credit soon    → 30 pts
    - Interested in investments   → 20 pts
    - Has existing debt           → 20 pts

    Premium threshold: 70+
    """
    score = score_ignacia(data)
    lead = Lead(
        id=str(uuid.uuid4()),
        channel=InfluencerChannel.ignacia_rios,
        full_name=data.full_name,
        email=data.email,
        contact=data.phone,
        score=score,
        raw_data=data.model_dump(),
        created_at=_now(),
    )
    save_lead(lead)
    return lead


@app.post("/leads/max", response_model=Lead, status_code=201, tags=["Capture"])
def capture_max(data: MaxLeadInput):
    """
    Register a lead from the Max Andrade funnel (digital business niche).

    Scoring criteria:
    - Has an active business       → 30 pts
    - Revenue ≥ $500 USD/month     → 30 pts
    - Defined niche                → 20 pts
    - Ready to invest              → 20 pts

    Premium threshold: 70+
    """
    score = score_max(data)
    lead = Lead(
        id=str(uuid.uuid4()),
        channel=InfluencerChannel.max_andrade,
        full_name=data.full_name,
        email=data.email,
        contact=data.whatsapp,
        score=score,
        raw_data=data.model_dump(),
        created_at=_now(),
    )
    save_lead(lead)
    return lead


@app.get("/leads", response_model=list[Lead], tags=["Query"])
def list_leads(
    channel: Optional[InfluencerChannel] = Query(None, description="Filter by influencer channel"),
    tier: Optional[LeadTier] = Query(None, description="Filter by lead tier (premium / standard)"),
):
    """
    Retrieve all leads. Optionally filter by channel and/or tier.
    Results are sorted newest first.
    """
    return get_leads(channel=channel, tier=tier)


@app.get("/leads/{lead_id}", response_model=Lead, tags=["Query"])
def get_lead(lead_id: str):
    """Retrieve a single lead by its UUID."""
    lead = get_lead_by_id(lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


@app.get("/stats", tags=["Reports"])
def get_stats():
    """
    Lead counts grouped by channel and tier.
    Useful for Pablo's daily dashboard.
    """
    return {
        "ignacia_rios": count_by_tier(InfluencerChannel.ignacia_rios),
        "max_andrade": count_by_tier(InfluencerChannel.max_andrade),
    }
