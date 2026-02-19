"""
Baku Agency — MAIN Agent Platform

  GET  /              → Web UI (chat + leads + agente autónomo)
  POST /chat          → Chat con BAKU_MASTER vía Ollama
  POST /leads         → Registrar nuevo lead
  GET  /leads         → Listar leads (filtrar por status/source)
  GET  /leads/{id}    → Detalle de un lead
  PATCH /leads/{id}/status → Actualizar estado del lead
  GET  /stats         → Estadísticas del CRM

  GET  /agent/status  → Estado del agente autónomo y scheduler
  POST /agent/trigger → Dispara ciclo autónomo inmediatamente
  GET  /agent/tasks   → Cola de tareas del agente
  POST /agent/tasks   → Crear tarea manualmente
  GET  /agent/log     → Historial de actividad del agente
  GET  /agent/memory  → Memoria persistente del agente

  GET  /gateway/status → Estado de la conexión con OpenClaw Gateway
"""

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent import chat
from gateway.bridge import get_status as gateway_get_status, start_bridge, stop_bridge
from autonomous.memory import (
    create_task,
    get_all_memory,
    get_all_tasks,
    get_recent_activity,
)
from autonomous.scheduler import (
    get_scheduler_status,
    shutdown,
    startup,
    trigger_cycle_now,
)
from leads_engine.models import Lead, LeadInput, LeadStatus
from leads_engine.storage import get_lead_by_id, get_leads, get_stats, save_lead, update_status


# ── Lifespan (startup / shutdown) ──────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await startup()          # init DB, seed tasks, start scheduler
    await start_bridge()     # conectar BAKU_MASTER al OpenClaw Gateway
    yield
    await stop_bridge()      # desconectar del Gateway
    await shutdown()         # stop scheduler cleanly


# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Baku Agency — MAIN Agent Platform",
    version="2.0.0",
    docs_url="/api/docs",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index():
    return Path("static/index.html").read_text(encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Chat ───────────────────────────────────────────────────────────────────────

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
    """Envía un mensaje a BAKU_MASTER."""
    result = await chat([m.model_dump() for m in req.messages])
    return ChatResponse(**result)


# ── Leads ──────────────────────────────────────────────────────────────────────

@app.post("/leads", response_model=Lead, status_code=201, tags=["Leads"])
def create_lead(data: LeadInput):
    """Registra un nuevo lead."""
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
    """Lista leads. Filtra por status y/o source."""
    return get_leads(status=status, source=source)


@app.get("/leads/{lead_id}", response_model=Lead, tags=["Leads"])
def get_lead(lead_id: str):
    lead = get_lead_by_id(lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead no encontrado")
    return lead


class StatusUpdate(BaseModel):
    status: LeadStatus


@app.patch("/leads/{lead_id}/status", response_model=Lead, tags=["Leads"])
def patch_status(lead_id: str, body: StatusUpdate):
    """Actualiza el estado de un lead."""
    lead = update_status(lead_id, body.status)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead no encontrado")
    return lead


@app.get("/stats", tags=["Leads"])
def stats():
    """Conteo de leads por estado y fuente."""
    return get_stats()


# ── Gateway ────────────────────────────────────────────────────────────────

@app.get("/gateway/status", tags=["Gateway"])
def gateway_status():
    """Estado de la conexión de BAKU_MASTER con el OpenClaw Gateway."""
    return gateway_get_status()


# ── Autonomous Agent ───────────────────────────────────────────────────────────

@app.get("/agent/status", tags=["Agente Autónomo"])
def agent_status():
    """Estado actual del agente autónomo y su scheduler."""
    return get_scheduler_status()


@app.post("/agent/trigger", tags=["Agente Autónomo"])
async def agent_trigger(background_tasks: BackgroundTasks):
    """Dispara un ciclo autónomo inmediatamente (no bloquea)."""
    background_tasks.add_task(trigger_cycle_now)
    return {"message": "Ciclo autónomo iniciado en background"}


@app.get("/agent/tasks", tags=["Agente Autónomo"])
def agent_tasks(limit: int = Query(50, ge=1, le=200)):
    """Cola de tareas del agente (historial + pendientes)."""
    return get_all_tasks(limit=limit)


class TaskCreate(BaseModel):
    title: str
    description: str = ""
    type: str = "general"
    priority: int = 5


@app.post("/agent/tasks", status_code=201, tags=["Agente Autónomo"])
def create_agent_task(body: TaskCreate):
    """Crea una tarea manualmente en la cola del agente."""
    task = create_task(
        title=body.title,
        description=body.description,
        task_type=body.type,
        priority=body.priority,
    )
    return task


@app.get("/agent/log", tags=["Agente Autónomo"])
def agent_log(limit: int = Query(50, ge=1, le=500)):
    """Historial de actividad del agente (más reciente primero)."""
    return get_recent_activity(limit=limit)


@app.get("/agent/memory", tags=["Agente Autónomo"])
def agent_memory():
    """Memoria estratégica persistente del agente."""
    return get_all_memory()
