"""
BAKU_MASTER — Scheduler autónomo (APScheduler).

Ciclos programados:
  • Ciclo principal      → cada 30 minutos
  • Reporte diario       → cada día a las 08:00 UTC (05:00 hora Chile)
  • Análisis de leads    → cada 2 horas

Arranque y apagado se integran en el lifespan de FastAPI.
"""

import asyncio
import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from autonomous.loop import run_agent_cycle
from autonomous.memory import create_task, init_db, log_activity

CYCLE_MINUTES = int(os.getenv("BAKU_CYCLE_MINUTES", "30"))

_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="America/Santiago")
    return _scheduler


# ── Scheduled jobs ─────────────────────────────────────────────────────────────

async def _daily_report_job() -> None:
    """Encola reporte diario automático."""
    create_task(
        title="Reporte diario ejecutivo",
        description="Genera reporte completo del día: leads, actividad del agente, proyecciones.",
        task_type="report",
        priority=8,
    )
    log_activity("Reporte diario encolado por scheduler", level="info")


async def _leads_check_job() -> None:
    """Verifica leads en riesgo cada 2 horas."""
    create_task(
        title="Revisión de leads en riesgo",
        description="Identifica leads sin contactar y contactados sin respuesta. Propone acciones.",
        task_type="lead_search",
        priority=7,
    )
    log_activity("Revisión de leads encolada por scheduler", level="info")


# ── Lifecycle ──────────────────────────────────────────────────────────────────

async def startup() -> None:
    """Inicializa DB, crea tareas de arranque y lanza el scheduler."""
    init_db()
    log_activity("BAKU_MASTER iniciando en modo autónomo...", level="info")

    # Seed initial tasks on first run
    _seed_initial_tasks()

    scheduler = get_scheduler()

    # Main autonomous cycle
    scheduler.add_job(
        run_agent_cycle,
        trigger=IntervalTrigger(minutes=CYCLE_MINUTES),
        id="main_cycle",
        name="BAKU_MASTER — Ciclo Principal",
        replace_existing=True,
    )

    # Daily report at 08:00 UTC (05:00 Chile)
    scheduler.add_job(
        _daily_report_job,
        trigger=CronTrigger(hour=8, minute=0),
        id="daily_report",
        name="BAKU_MASTER — Reporte Diario",
        replace_existing=True,
    )

    # Leads check every 2 hours
    scheduler.add_job(
        _leads_check_job,
        trigger=IntervalTrigger(hours=2),
        id="leads_check",
        name="BAKU_MASTER — Revisión de Leads",
        replace_existing=True,
    )

    scheduler.start()
    log_activity(
        f"Scheduler activo — ciclo cada {CYCLE_MINUTES} min | reporte diario 08:00 UTC | leads cada 2h",
        level="success",
    )

    # Run first cycle immediately in background
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


def get_scheduler_status() -> dict:
    """Retorna estado del scheduler para la API."""
    scheduler = get_scheduler()
    jobs = []
    if scheduler.running:
        for job in scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            })
    return {
        "running": scheduler.running,
        "cycle_minutes": CYCLE_MINUTES,
        "jobs": jobs,
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
                "Objetivo: generar leads B2B en Chile interesados en marketing digital. "
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
        create_task(**t)

    set_memory("seeded", True)
    log_activity(
        f"Tareas iniciales creadas: {len(initial_tasks)} tareas en cola",
        level="success",
    )
