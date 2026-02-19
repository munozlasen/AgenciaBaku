"""
Baku Agency — MAIN Agent Platform

  GET  /              → Web UI (chat + leads dashboard)
  POST /chat          → Chat with BAKU_MASTER via Ollama
  POST /leads         → Register a new lead
  GET  /leads         → List leads (filter by status / source)
  PATCH /leads/{id}/status → Update lead status
  GET  /stats         → Lead statistics
"""

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent import chat
from leads_engine.models import Lead, LeadInput, LeadStatus
from leads_engine.storage import save_lead, get_leads, get_lead_by_id, update_status, get_stats

app = FastAPI(
    title="Baku Agency — MAIN Agent Platform",
    version="1.0.0",
    docs_url="/api/docs",
)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index():
    return Path("static/index.html").read_text(encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Chat ──────────────────────────────────────────────────────────────────────

class Message(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: list[Message]

class ChatResponse(BaseModel):
    content: str
    thinking: str
    mode: str
    via: str = ""
    error: Optional[str] = None


@app.post("/chat", response_model=ChatResponse, tags=["Agent"])
async def chat_endpoint(req: ChatRequest):
    """Send a message to BAKU_MASTER (OpenClaw MAIN agent via Ollama)."""
    result = await chat([m.model_dump() for m in req.messages])
    return ChatResponse(**result)


# ── Leads ─────────────────────────────────────────────────────────────────────

@app.post("/leads", response_model=Lead, status_code=201, tags=["Leads"])
def create_lead(data: LeadInput):
    """Register a new lead."""
    lead = Lead(
        id=str(uuid.uuid4()),
        full_name=data.full_name,
        email=data.email,
        phone=data.phone,
        source=data.source,
        notes=data.notes or "",
        status=LeadStatus.new,
        created_at=_now(),
    )
    save_lead(lead)
    return lead


@app.get("/leads", response_model=list[Lead], tags=["Leads"])
def list_leads(
    status: Optional[LeadStatus] = Query(None),
    source: Optional[str] = Query(None),
):
    """List leads. Filter by status and/or source."""
    return get_leads(status=status, source=source)


@app.get("/leads/{lead_id}", response_model=Lead, tags=["Leads"])
def get_lead(lead_id: str):
    lead = get_lead_by_id(lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


class StatusUpdate(BaseModel):
    status: LeadStatus

@app.patch("/leads/{lead_id}/status", response_model=Lead, tags=["Leads"])
def patch_status(lead_id: str, body: StatusUpdate):
    """Update the status of a lead."""
    lead = update_status(lead_id, body.status)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


@app.get("/stats", tags=["Leads"])
def stats():
    """Lead counts by status and source."""
    return get_stats()
