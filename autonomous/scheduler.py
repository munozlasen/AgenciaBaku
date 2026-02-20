"""
BAKU_MASTER — Scheduler autónomo (APScheduler).

Ciclos programados (zona horaria: America/Santiago):
  • Ciclo principal      → cada 30 minutos
  • Reporte diario       → cada día a las 09:00 hrs (Santiago)
  • Análisis de leads    → cada 2 horas

Todas las horas se manejan en America/Santiago.
"""

import asyncio
import os
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger

from autonomous.loop import run_agent_cycle
from autonomous.memory import create_task, init_db, log_activity

TZ = ZoneInfo("America/Santiago")
CYCLE_MINUTES = int(os.getenv("BAKU_CYCLE_MINUTES", "30"))

_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="America/Santiago")
    return _scheduler


# ── Scheduled jobs ─────────────────────────────────────────────────────────────

async def _daily_report_job() -> None:
    """Encola reporte diario automático (09:00 Santiago)."""
    now = datetime.now(TZ).strftime("%d/%m/%Y %H:%M hrs")
    create_task(
        title=f"Reporte diario ejecutivo — {now}",
        description="Genera reporte completo del día: leads, actividad del agente, proyecciones. Incluir alertas activas.",
        task_type="report",
        priority=8,
    )
    log_activity("Reporte diario encolado por scheduler", level="info")


async def _leads_check_job() -> None:
    """Verifica leads en riesgo cada 2 horas."""
    create_task(
        title="Revisión de leads en riesgo",
        description="Identificar leads sin contactar y contactados sin respuesta. Proponer acciones concretas.",
        task_type="lead_search",
        priority=7,
    )
    log_activity("Revisión de leads encolada por scheduler", level="info")


# ── Lifecycle ──────────────────────────────────────────────────────────────────

async def startup() -> None:
    """Inicializa DB, crea tareas de arranque y lanza el scheduler."""
    init_db()
    log_activity("BAKU_MASTER iniciando en modo autónomo...", level="info")

    _seed_initial_tasks()

    scheduler = get_scheduler()

    scheduler.add_job(
        run_agent_cycle,
        trigger=IntervalTrigger(minutes=CYCLE_MINUTES, timezone=TZ),
        id="main_cycle",
        name=f"BAKU_MASTER — Ciclo Principal (cada {CYCLE_MINUTES} min)",
        replace_existing=True,
    )

    scheduler.add_job(
        _daily_report_job,
        trigger=CronTrigger(hour=9, minute=0, timezone=TZ),
        id="daily_report",
        name="BAKU_MASTER — Reporte Diario (09:00 Santiago)",
        replace_existing=True,
    )

    scheduler.add_job(
        _leads_check_job,
        trigger=IntervalTrigger(hours=2, timezone=TZ),
        id="leads_check",
        name="BAKU_MASTER — Revisión de Leads (cada 2h)",
        replace_existing=True,
    )

    scheduler.start()
    log_activity(
        f"Scheduler activo — ciclo cada {CYCLE_MINUTES} min | reporte 09:00 Santiago | leads cada 2h",
        level="success",
    )

    asyncio.create_task(run_agent_cycle())
    log_activity("Primer ciclo autónomo disparado", level="info")


async def shutdown() -> None:
    """Detiene el scheduler limpiamente."""
    scheduler = get_scheduler()
    if scheduler.running:
        scheduler.shutdown(wait=False)
    log_activity("BAKU_MASTER detenido", level="warning")


async def trigger_cycle_now() -> None:
    """Dispara un ciclo autónomo inmediatamente (llamado desde API)."""
    log_activity("Ciclo manual disparado desde API", level="info")
    await run_agent_cycle()


# ── Cron Management API ────────────────────────────────────────────────────────

def list_jobs() -> list[dict]:
    """Lista todos los trabajos programados con su próxima ejecución."""
    scheduler = get_scheduler()
    jobs = []
    for job in scheduler.get_jobs():
        next_run = None
        if job.next_run_time:
            # Convertir a Santiago
            nr = job.next_run_time.astimezone(TZ)
            next_run = nr.strftime("%d/%m/%Y %H:%M hrs")
            next_run_iso = nr.isoformat()
        else:
            next_run_iso = None

        # Determinar tipo de trigger
        trigger_type = type(job.trigger).__name__.replace("Trigger", "").lower()
        trigger_desc = _describe_trigger(job)

        jobs.append({
            "id": job.id,
            "name": job.name,
            "trigger_type": trigger_type,
            "trigger_desc": trigger_desc,
            "next_run": next_run,
            "next_run_iso": next_run_iso,
            "is_paused": job.next_run_time is None,
        })
    return jobs


def _describe_trigger(job) -> str:
    """Genera descripción human-readable del trigger."""
    t = job.trigger
    if isinstance(t, IntervalTrigger):
        fields = {f.name: f.value for f in t.fields if not f.is_default}
        parts = []
        if fields.get("weeks"):
            parts.append(f"cada {fields['weeks']} semana(s)")
        if fields.get("days"):
            parts.append(f"cada {fields['days']} día(s)")
        if fields.get("hours"):
            parts.append(f"cada {fields['hours']} hora(s)")
        if fields.get("minutes"):
            parts.append(f"cada {fields['minutes']} min")
        if fields.get("seconds"):
            parts.append(f"cada {fields['seconds']} seg")
        return " | ".join(parts) if parts else "intervalo"
    elif isinstance(t, CronTrigger):
        fields = {f.name: str(f) for f in t.fields if not f.is_default}
        return f"cron: {' '.join(str(f) for f in t.fields)}"
    elif isinstance(t, DateTrigger):
        return f"una vez: {t.run_date.strftime('%d/%m/%Y %H:%M')}"
    return str(t)


def add_custom_job(
    title: str,
    description: str,
    task_type: str,
    priority: int,
    trigger_type: str,
    trigger_params: dict,
) -> dict:
    """
    Agrega un nuevo job personalizado al scheduler.

    trigger_type: 'interval' | 'cron' | 'date'
    trigger_params ejemplos:
      interval: {minutes: 60} | {hours: 4} | {days: 1}
      cron: {hour: 10, minute: 30} | {day_of_week: 'mon', hour: 9}
      date: {run_date: '2026-02-20 10:00:00'}
    """
    scheduler = get_scheduler()
    job_id = f"custom_{uuid.uuid4().hex[:8]}"

    # Función a ejecutar
    async def _custom_job():
        create_task(
            title=title,
            description=description,
            task_type=task_type,
            priority=priority,
        )
        log_activity(f"Tarea encolada por job programado: {title}", level="info")

    # Crear trigger
    if trigger_type == "interval":
        trigger = IntervalTrigger(timezone=TZ, **trigger_params)
    elif trigger_type == "cron":
        trigger = CronTrigger(timezone=TZ, **trigger_params)
    elif trigger_type == "date":
        run_date = trigger_params.get("run_date")
        trigger = DateTrigger(run_date=run_date, timezone=TZ)
    else:
        raise ValueError(f"trigger_type inválido: {trigger_type}. Usa: interval | cron | date")

    job = scheduler.add_job(
        _custom_job,
        trigger=trigger,
        id=job_id,
        name=f"Custom: {title[:50]}",
    )

    next_run = None
    if job.next_run_time:
        nr = job.next_run_time.astimezone(TZ)
        next_run = nr.strftime("%d/%m/%Y %H:%M hrs")

    log_activity(f"Job personalizado creado: {title} [{job_id}]", level="success")
    return {
        "id": job_id,
        "name": job.name,
        "trigger_type": trigger_type,
        "next_run": next_run,
    }


def remove_job(job_id: str) -> bool:
    """Elimina un job del scheduler. Retorna True si se eliminó."""
    scheduler = get_scheduler()
    try:
        scheduler.remove_job(job_id)
        log_activity(f"Job eliminado: {job_id}", level="warning")
        return True
    except Exception:
        return False


def pause_job(job_id: str) -> bool:
    """Pausa un job. Retorna True si tuvo éxito."""
    scheduler = get_scheduler()
    try:
        scheduler.pause_job(job_id)
        log_activity(f"Job pausado: {job_id}", level="info")
        return True
    except Exception:
        return False


def resume_job(job_id: str) -> bool:
    """Reanuda un job pausado. Retorna True si tuvo éxito."""
    scheduler = get_scheduler()
    try:
        scheduler.resume_job(job_id)
        log_activity(f"Job reanudado: {job_id}", level="info")
        return True
    except Exception:
        return False


async def trigger_job_now(job_id: str) -> bool:
    """Ejecuta un job inmediatamente. Retorna True si tuvo éxito."""
    scheduler = get_scheduler()
    job = scheduler.get_job(job_id)
    if not job:
        return False
    try:
        asyncio.create_task(job.func())
        log_activity(f"Job ejecutado manualmente: {job_id}", level="info")
        return True
    except Exception:
        return False


def get_scheduler_status() -> dict:
    """Retorna estado del scheduler para la API."""
    scheduler = get_scheduler()
    now = datetime.now(TZ)
    return {
        "running": scheduler.running,
        "cycle_minutes": CYCLE_MINUTES,
        "timezone": "America/Santiago",
        "now_santiago": now.strftime("%d/%m/%Y %H:%M hrs"),
        "jobs": list_jobs(),
    }


# ── Seed ───────────────────────────────────────────────────────────────────────

def _seed_initial_tasks() -> None:
    """Crea tareas iniciales la primera vez que arranca el agente."""
    from autonomous.memory import get_memory, set_memory

    if get_memory("seeded"):
        return

    initial_tasks = [
        {
            "title": "Análisis competitivo del mercado chileno de agencias digitales",
            "description": (
                "Investigar las principales agencias de marketing digital en Chile. "
                "Identificar propuestas de valor, precios, servicios y debilidades. "
                "Encontrar el posicionamiento diferenciador óptimo para BAKU."
            ),
            "type": "market_research",
            "priority": 10,
        },
        {
            "title": "Definir propuesta de valor y servicios de BAKU Agency",
            "description": (
                "Basado en el análisis competitivo, diseñar los paquetes de servicios, "
                "precios en CLP, propuesta de valor diferenciadora y narrativa corporativa. "
                "Incluir modelo financiero básico con CPL objetivo y CAC sostenible."
            ),
            "type": "strategy",
            "priority": 9,
        },
        {
            "title": "Crear primer copy para Meta Ads — captación de leads",
            "description": (
                "Generar 3 variantes de copy para anuncios en Facebook/Instagram. "
                "Objetivo: leads B2B en Chile interesados en marketing digital. "
                "Incluir titular, descripción y CTA. Presupuesto test sugerido."
            ),
            "type": "content_gen",
            "priority": 8,
        },
        {
            "title": "Reporte de estado inicial — Fase 0 BAKU",
            "description": (
                "Generar reporte ejecutivo del punto de partida: "
                "estado actual, tareas planificadas, hoja de ruta 30 días, riesgos y próximos pasos."
            ),
            "type": "report",
            "priority": 7,
        },
    ]

    for t in initial_tasks:
        create_task(
            title=t["title"],
            description=t["description"],
            task_type=t["type"],
            priority=t["priority"],
        )

    set_memory("seeded", True)
    log_activity(
        f"Tareas iniciales creadas: {len(initial_tasks)} tareas en cola",
        level="success",
    )
