"""
Baku Agency — MAIN Agent Platform

  GET  /              → Web UI (chat + leads + agente autónomo)
  POST /chat          → Chat con BAKU_MASTER vía Ollama
  POST /search        → Búsqueda web directa

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

  GET  /cron/jobs             → Lista todos los jobs programados
  POST /cron/jobs             → Crea un nuevo job
  POST /cron/jobs/{id}/trigger → Ejecuta un job ahora
  POST /cron/jobs/{id}/pause  → Pausa un job
  POST /cron/jobs/{id}/resume → Reanuda un job
  DELETE /cron/jobs/{id}      → Elimina un job

  GET  /files            → Lista archivos generados
  POST /files/generate   → Genera un archivo (docx/xlsx/pdf/txt/md)
  GET  /files/{filename} → Descarga un archivo
  DELETE /files/{filename} → Elimina un archivo

  GET  /gateway/status  → Estado de la conexión con OpenClaw Gateway
  GET  /gateway/probe   → Últimos frames recibidos del Gateway (debug)
"""

import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
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
    add_custom_job,
    get_scheduler_status,
    list_jobs,
    pause_job,
    remove_job,
    resume_job,
    shutdown,
    startup,
    trigger_cycle_now,
    trigger_job_now,
)
from leads_engine.models import Lead, LeadInput, LeadStatus
from leads_engine.storage import get_lead_by_id, get_leads, get_stats, save_lead, update_status
from files_engine.generator import (
    generate_file as gen_file,
    list_files,
    delete_file,
    OUTPUT_DIR,
)

TZ = ZoneInfo("America/Santiago")


def _now_santiago() -> str:
    return datetime.now(TZ).isoformat()


# ── Lifespan ────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await startup()          # init DB, seed tasks, start scheduler
    await start_bridge()     # conectar BAKU_MASTER al OpenClaw Gateway
    yield
    await stop_bridge()      # desconectar del Gateway
    await shutdown()         # stop scheduler cleanly


# ── App ─────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Baku Agency — MAIN Agent Platform",
    version="3.0.0",
    docs_url="/api/docs",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index():
    return Path("static/index.html").read_text(encoding="utf-8")


# ── Chat ─────────────────────────────────────────────────────────────────────────

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


# ── Web Search ───────────────────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str
    purpose: str = "general"


@app.post("/search", tags=["Agent"])
async def web_search_endpoint(req: SearchRequest):
    """Búsqueda web directa — retorna resultados de DuckDuckGo."""
    from autonomous.tools import _web_search
    result = await _web_search(req.query, req.purpose, task_id=None)
    return {"query": req.query, "result": result}


# ── Leads ─────────────────────────────────────────────────────────────────────────

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
        created_at=_now_santiago(),
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


# ── Gateway ─────────────────────────────────────────────────────────────────────

@app.get("/gateway/status", tags=["Gateway"])
def gateway_status():
    """Estado de la conexión de BAKU_MASTER con el OpenClaw Gateway."""
    return gateway_get_status()


@app.get("/gateway/probe", tags=["Gateway"])
def gateway_probe():
    """Últimos frames recibidos del Gateway (útil para depurar protocolo)."""
    status = gateway_get_status()
    return {
        "probe_log": status.get("probe_log", []),
        "last_error": status.get("last_error"),
        "reconnect_attempts": status.get("reconnect_attempts", 0),
    }


# ── Autonomous Agent ─────────────────────────────────────────────────────────────

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


# ── Cron Management ──────────────────────────────────────────────────────────────

@app.get("/cron/jobs", tags=["Cron"])
def cron_list():
    """Lista todos los jobs programados del scheduler."""
    return list_jobs()


class CronJobCreate(BaseModel):
    title: str
    description: str = ""
    task_type: str = "general"
    priority: int = 5
    trigger_type: str  # interval | cron | date
    trigger_params: dict  # {minutes: 60} | {hour: 9, minute: 0} | {run_date: "2026-03-01 10:00"}


@app.post("/cron/jobs", status_code=201, tags=["Cron"])
def cron_create(body: CronJobCreate):
    """Crea un nuevo job programado. trigger_type: interval | cron | date"""
    try:
        job = add_custom_job(
            title=body.title,
            description=body.description,
            task_type=body.task_type,
            priority=body.priority,
            trigger_type=body.trigger_type,
            trigger_params=body.trigger_params,
        )
        return job
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/cron/jobs/{job_id}/trigger", tags=["Cron"])
async def cron_trigger(job_id: str):
    """Ejecuta un job inmediatamente."""
    ok = await trigger_job_now(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' no encontrado")
    return {"message": f"Job '{job_id}' ejecutado"}


@app.post("/cron/jobs/{job_id}/pause", tags=["Cron"])
def cron_pause(job_id: str):
    """Pausa un job."""
    ok = pause_job(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' no encontrado")
    return {"message": f"Job '{job_id}' pausado"}


@app.post("/cron/jobs/{job_id}/resume", tags=["Cron"])
def cron_resume(job_id: str):
    """Reanuda un job pausado."""
    ok = resume_job(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' no encontrado")
    return {"message": f"Job '{job_id}' reanudado"}


@app.delete("/cron/jobs/{job_id}", tags=["Cron"])
def cron_delete(job_id: str):
    """Elimina un job del scheduler."""
    ok = remove_job(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' no encontrado")
    return {"message": f"Job '{job_id}' eliminado"}


# ── Files ────────────────────────────────────────────────────────────────────────

@app.get("/files", tags=["Archivos"])
def files_list():
    """Lista todos los archivos generados disponibles para descarga."""
    return list_files()


class FileGenerateRequest(BaseModel):
    title: str
    content: str
    format: str  # docx | xlsx | pdf | txt | md
    subtitle: str = ""


@app.post("/files/generate", tags=["Archivos"])
def files_generate(body: FileGenerateRequest):
    """Genera un archivo en el formato solicitado."""
    result = gen_file(
        title=body.title,
        content=body.content,
        file_format=body.format,
        subtitle=body.subtitle,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Error generando archivo"))
    return result


@app.get("/files/{filename}", tags=["Archivos"])
def files_download(filename: str):
    """Descarga un archivo generado."""
    # Validar que el filename no tenga path traversal
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Nombre de archivo inválido")
    path = OUTPUT_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")

    # Determinar media type
    ext = path.suffix.lower()
    media_types = {
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".pdf": "application/pdf",
        ".txt": "text/plain; charset=utf-8",
        ".md": "text/markdown; charset=utf-8",
    }
    media_type = media_types.get(ext, "application/octet-stream")
    return FileResponse(path=str(path), media_type=media_type, filename=filename)


@app.delete("/files/{filename}", tags=["Archivos"])
def files_delete(filename: str):
    """Elimina un archivo generado."""
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Nombre de archivo inválido")
    ok = delete_file(filename)
    if not ok:
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    return {"message": f"Archivo '{filename}' eliminado"}
