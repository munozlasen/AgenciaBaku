"""
BAKU_MASTER — Herramientas autónomas.

Herramientas disponibles para el agente:
  web_search           → Busca información en internet (DuckDuckGo)
  generate_content     → Genera contenido con Ollama
  analyze_leads        → Analiza estado del CRM
  create_strategic_task → Encola nueva tarea estratégica
  generate_report      → Genera reporte de situación
  save_memory          → Guarda información importante para uso futuro
"""

import json
from datetime import datetime, timezone
from typing import Optional

import httpx

from autonomous.memory import (
    create_task,
    get_memory,
    get_recent_activity,
    log_activity,
    set_memory,
)

# ── Tool specs (Ollama function-calling format) ────────────────────────────────

TOOLS_SPEC: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Busca información real en internet sobre mercado chileno, competencia, "
                "leads potenciales, noticias del sector, o cualquier dato externo necesario."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Término de búsqueda preciso y estratégico",
                    },
                    "purpose": {
                        "type": "string",
                        "description": "Propósito: market_research | lead_discovery | competitor_analysis | trend_analysis",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_content",
            "description": (
                "Genera contenido profesional de alta calidad: posts para redes, "
                "copy para ads, plantillas de email, análisis estratégicos, reportes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content_type": {
                        "type": "string",
                        "description": "Tipo: post_linkedin | post_instagram | ad_copy_meta | ad_copy_google | email_template | strategic_analysis | pitch_deck_section",
                    },
                    "topic": {
                        "type": "string",
                        "description": "Tema central del contenido",
                    },
                    "context": {
                        "type": "string",
                        "description": "Contexto adicional, datos relevantes, tono deseado",
                    },
                },
                "required": ["content_type", "topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_leads",
            "description": "Analiza el estado actual del CRM de leads y propone acciones concretas.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Tipo de análisis: overview | identify_at_risk | suggest_followup | conversion_audit",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_strategic_task",
            "description": "Crea y encola una nueva tarea estratégica para ejecución autónoma futura.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Título conciso de la tarea"},
                    "description": {"type": "string", "description": "Descripción detallada y objetivo"},
                    "type": {
                        "type": "string",
                        "description": "Categoría: lead_search | content_gen | market_research | report | outreach | strategy | financial_model",
                    },
                    "priority": {
                        "type": "integer",
                        "description": "Prioridad 1-10 (10 = máxima urgencia)",
                    },
                },
                "required": ["title", "description", "type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_report",
            "description": "Genera un reporte ejecutivo del estado actual de la agencia.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "description": "Alcance: daily | weekly | leads_status | financial_projection | branding_phase",
                    },
                },
                "required": ["scope"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "Guarda información estratégica importante en la memoria persistente del agente.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Clave identificadora única"},
                    "value": {"type": "string", "description": "Información a guardar"},
                },
                "required": ["key", "value"],
            },
        },
    },
]


# ── Dispatcher ─────────────────────────────────────────────────────────────────

async def execute_tool(tool_name: str, args: dict, task_id: Optional[str] = None) -> str:
    """Execute a tool by name and return the string result."""
    try:
        match tool_name:
            case "web_search":
                return await _web_search(
                    args.get("query", ""),
                    args.get("purpose", "general"),
                    task_id,
                )
            case "generate_content":
                return await _generate_content(
                    args.get("content_type", "post_linkedin"),
                    args.get("topic", ""),
                    args.get("context", ""),
                    task_id,
                )
            case "analyze_leads":
                return await _analyze_leads(args.get("action", "overview"), task_id)
            case "create_strategic_task":
                return _create_strategic_task(args, task_id)
            case "generate_report":
                return await _generate_report(args.get("scope", "daily"), task_id)
            case "save_memory":
                set_memory(args["key"], args["value"])
                log_activity(f"Memoria guardada: {args['key']}", task_id=task_id)
                return f"Guardado en memoria: {args['key']}"
            case _:
                return f"Herramienta desconocida: {tool_name}"
    except Exception as e:
        msg = f"Error en herramienta '{tool_name}': {e}"
        log_activity(msg, level="error", task_id=task_id)
        return msg


# ── Implementations ────────────────────────────────────────────────────────────

async def _web_search(query: str, purpose: str, task_id: Optional[str]) -> str:
    log_activity(f"Buscando: {query}", task_id=task_id)
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
                headers={"User-Agent": "BakuAgency/1.0"},
            )
            data = r.json()

        abstract = data.get("AbstractText", "").strip()
        related = [
            t.get("Text", "")
            for t in data.get("RelatedTopics", [])[:6]
            if isinstance(t, dict) and "Text" in t
        ]

        out = [f"BÚSQUEDA: {query}", f"Propósito: {purpose}"]
        if abstract:
            out.append(f"\nResumen:\n{abstract}")
        if related:
            out.append("\nTemas relacionados:")
            out.extend(f"  • {t}" for t in related if t)
        if not abstract and not related:
            out.append("\n(Sin resultados directos — se recomienda búsqueda manual o fuente alternativa)")

        result = "\n".join(out)
        log_activity(f"Búsqueda completada: {query[:60]}", level="success", task_id=task_id)
        return result

    except Exception as e:
        return f"Error en búsqueda web: {e}"


async def _generate_content(
    content_type: str, topic: str, context: str, task_id: Optional[str]
) -> str:
    from agent import chat as ollama_chat  # imported here to avoid circular at module load

    log_activity(f"Generando {content_type}: {topic}", task_id=task_id)

    type_instructions = {
        "post_linkedin": "Post profesional para LinkedIn, tono ejecutivo, máximo 1300 caracteres.",
        "post_instagram": "Post para Instagram en español chileno, con emojis estratégicos, hashtags relevantes.",
        "ad_copy_meta": "Copy para anuncio en Meta Ads (Facebook/Instagram). Titular + descripción + CTA.",
        "ad_copy_google": "Copy para Google Ads: 3 titulares (30 char c/u) + 2 descripciones (90 char c/u).",
        "email_template": "Plantilla de email de prospección en español chileno. Asunto + cuerpo + CTA.",
        "strategic_analysis": "Análisis estratégico profundo con estructura: Contexto → Diagnóstico → Propuesta → Riesgo → Próximo paso.",
        "pitch_deck_section": "Sección de pitch deck ejecutivo para inversores o clientes empresariales chilenos.",
        "financial_model": "Modelo financiero simplificado con CPL, CAC, ROAS, punto de equilibrio y proyección 3 meses.",
    }

    instructions = type_instructions.get(content_type, f"Contenido de tipo '{content_type}'.")
    prompt = (
        f"Genera el siguiente contenido para BAKU Agency:\n\n"
        f"TIPO: {instructions}\n"
        f"TEMA: {topic}\n"
        f"CONTEXTO ADICIONAL: {context or 'Ninguno'}\n\n"
        f"Foco en mercado chileno. Resultado profesional, listo para usar."
    )

    result = await ollama_chat([{"role": "user", "content": prompt}])
    content = result.get("content", "")
    log_activity(f"Contenido generado: {content_type} — {topic[:50]}", level="success", task_id=task_id)
    return content or "(Sin contenido generado)"


async def _analyze_leads(action: str, task_id: Optional[str]) -> str:
    from leads_engine.storage import get_leads, get_stats

    log_activity(f"Analizando leads: {action}", task_id=task_id)
    stats = get_stats()
    leads = get_leads()

    out = [
        "=== ANÁLISIS DE LEADS — BAKU CRM ===",
        f"Total: {stats.get('total', 0)}",
    ]
    for status, count in stats.get("by_status", {}).items():
        out.append(f"  {status}: {count}")

    if action == "identify_at_risk":
        new_leads = [l for l in leads if l.status == "new"]
        out.append(f"\nLeads sin contactar (riesgo de enfriamiento): {len(new_leads)}")
        if new_leads:
            out.append("ACCIÓN RECOMENDADA: Contactar en las próximas 24h.")
            for l in new_leads[:5]:
                out.append(f"  • {l.full_name} — {l.source} — {l.created_at[:10]}")

    elif action == "suggest_followup":
        contacted = [l for l in leads if l.status == "contacted"]
        out.append(f"\nEn seguimiento activo: {len(contacted)}")
        out.append("ACCIÓN: Evaluar calificación y avanzar a siguiente etapa.")

    elif action == "conversion_audit":
        converted = [l for l in leads if l.status == "converted"]
        total = stats.get("total", 1) or 1
        rate = round(len(converted) / total * 100, 1)
        out.append(f"\nTasa de conversión actual: {rate}%")
        if rate < 10:
            out.append("ALERTA: Conversión baja. Revisar calidad de leads y proceso de cierre.")

    log_activity("Análisis de leads completado", level="success", task_id=task_id)
    return "\n".join(out)


def _create_strategic_task(args: dict, task_id: Optional[str]) -> str:
    task = create_task(
        title=args["title"],
        description=args.get("description", ""),
        task_type=args.get("type", "general"),
        priority=args.get("priority", 5),
    )
    msg = f"Tarea creada [{task['id'][:8]}]: {args['title']}"
    log_activity(msg, level="info", task_id=task_id)
    return msg


async def _generate_report(scope: str, task_id: Optional[str]) -> str:
    from leads_engine.storage import get_stats

    log_activity(f"Generando reporte: {scope}", task_id=task_id)
    stats = get_stats()
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    memory = get_memory("strategic_context", {})
    recent_log = get_recent_activity(limit=10)

    lines = [
        f"=== REPORTE EJECUTIVO BAKU AGENCY — {scope.upper()} ===",
        f"Generado: {now_str}",
        "",
        "── LEADS ──",
        f"Total registrados: {stats.get('total', 0)}",
    ]
    for s, c in stats.get("by_status", {}).items():
        lines.append(f"  {s}: {c}")

    lines += [
        "",
        "── ACTIVIDAD RECIENTE DEL AGENTE ──",
    ]
    for a in recent_log[:5]:
        lines.append(f"  [{a['level'].upper()}] {a['message']}")

    if stats.get("total", 0) == 0:
        lines += [
            "",
            "── ALERTA CRÍTICA ──",
            "Sin leads registrados. Prioridad máxima: activar captación.",
            "Recomendación: lanzar campaña test en Meta Ads con presupuesto mínimo ($5.000 CLP/día).",
        ]

    if memory:
        lines += ["", "── CONTEXTO ESTRATÉGICO GUARDADO ──"]
        for k, v in list(memory.items())[:3]:
            lines.append(f"  {k}: {v}")

    report = "\n".join(lines)
    log_activity(f"Reporte {scope} generado", level="success", task_id=task_id)
    return report
