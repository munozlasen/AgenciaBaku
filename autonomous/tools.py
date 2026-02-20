"""
BAKU_MASTER — Herramientas autónomas.

Herramientas disponibles para el agente:
  web_search            → Busca información en internet (DuckDuckGo)
  generate_content      → Genera contenido con Ollama
  generate_file         → Genera archivos DOCX, XLSX, PDF, TXT, MD
  analyze_leads         → Analiza estado del CRM
  create_strategic_task → Encola nueva tarea estratégica
  generate_report       → Genera reporte de situación
  save_memory           → Guarda información importante para uso futuro
"""

import json
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional

import httpx

from autonomous.memory import (
    create_task,
    get_memory,
    get_recent_activity,
    log_activity,
    set_memory,
)

TZ = ZoneInfo("America/Santiago")

# ── Tool specs (Ollama function-calling format) ────────────────────────────────

TOOLS_SPEC: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Busca información real y actualizada en internet sobre mercado chileno, "
                "competencia, leads potenciales, noticias del sector, precios, tendencias, "
                "o cualquier dato externo necesario para decisiones estratégicas."
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
                        "description": "Propósito: market_research | lead_discovery | competitor_analysis | trend_analysis | general",
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
                "Genera contenido profesional de alta calidad en español chileno: "
                "posts para redes, copy para ads, plantillas de email, análisis "
                "estratégicos, reportes, secciones de pitch deck."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content_type": {
                        "type": "string",
                        "description": "Tipo: post_linkedin | post_instagram | ad_copy_meta | ad_copy_google | email_template | strategic_analysis | pitch_deck_section | financial_model",
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
            "name": "generate_file",
            "description": (
                "Genera un archivo listo para usar en el trabajo: propuestas en Word, "
                "modelos financieros en Excel, reportes en PDF. "
                "El archivo queda disponible para descarga en la interfaz."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Título del documento (también se usa como nombre de archivo)",
                    },
                    "content": {
                        "type": "string",
                        "description": "Contenido completo del documento en Markdown. Soporta # encabezados, **negrita**, - listas, tablas |col|col|",
                    },
                    "format": {
                        "type": "string",
                        "description": "Formato de salida: docx | xlsx | pdf | txt | md",
                    },
                    "subtitle": {
                        "type": "string",
                        "description": "Subtítulo opcional (aparece debajo del título)",
                    },
                },
                "required": ["title", "content", "format"],
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
    """Ejecuta una herramienta por nombre y retorna el resultado como string."""
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
            case "generate_file":
                return await _generate_file(
                    args.get("title", "Documento"),
                    args.get("content", ""),
                    args.get("format", "pdf"),
                    args.get("subtitle", ""),
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
    log_activity(f"Buscando en web: {query}", task_id=task_id)
    results_text = ""

    # Intento 1: duckduckgo-search (librería más confiable)
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, region="cl-es", max_results=6))
        if results:
            lines = [f"BÚSQUEDA WEB: {query}", f"Propósito: {purpose}", ""]
            for i, r in enumerate(results[:6], 1):
                lines.append(f"{i}. {r.get('title', '')}")
                lines.append(f"   {r.get('body', '')[:200]}")
                if r.get("href"):
                    lines.append(f"   Fuente: {r['href']}")
                lines.append("")
            results_text = "\n".join(lines)
            log_activity(f"Búsqueda completada: {query[:60]}", level="success", task_id=task_id)
            return results_text
    except Exception as e:
        log_activity(f"DDG search falló: {e} — intentando fallback", level="warning", task_id=task_id)

    # Fallback: DuckDuckGo Instant Answer API
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
                headers={"User-Agent": "BakuAgency/2.0 (+https://baku.cl)"},
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
            out.append("\n(Sin resultados directos. Considera búsqueda manual.)")

        result = "\n".join(out)
        log_activity(f"Búsqueda (fallback) completada: {query[:60]}", level="success", task_id=task_id)
        return result

    except Exception as e:
        return f"Error en búsqueda web: {e}"


async def _generate_content(
    content_type: str, topic: str, context: str, task_id: Optional[str]
) -> str:
    from agent import chat as ollama_chat

    log_activity(f"Generando {content_type}: {topic}", task_id=task_id)

    type_instructions = {
        "post_linkedin": "Post profesional para LinkedIn en español chileno, tono ejecutivo cercano, máximo 1300 caracteres.",
        "post_instagram": "Post para Instagram en español chileno con emojis estratégicos y hashtags relevantes del mercado local.",
        "ad_copy_meta": "Copy para anuncio en Meta Ads (Facebook/Instagram). Titular + descripción + CTA. Lenguaje directo y chileno.",
        "ad_copy_google": "Copy para Google Ads: 3 titulares (máx 30 char) + 2 descripciones (máx 90 char). En español chileno.",
        "email_template": "Plantilla de email de prospección en español chileno. Asunto + cuerpo + CTA. Natural, no robótico.",
        "strategic_analysis": "Análisis estratégico con estructura: Contexto → Diagnóstico → Propuesta → Riesgo → Próximo paso concreto.",
        "pitch_deck_section": "Sección de pitch deck ejecutivo para clientes empresariales chilenos. Claro, convincente, con datos.",
        "financial_model": "Modelo financiero con CPL, CAC, ROAS, punto de equilibrio y proyección 3 meses en CLP.",
    }

    instructions = type_instructions.get(content_type, f"Contenido de tipo '{content_type}'.")
    prompt = (
        f"Genera el siguiente contenido para BAKU Agency:\n\n"
        f"TIPO: {instructions}\n"
        f"TEMA: {topic}\n"
        f"CONTEXTO ADICIONAL: {context or 'Ninguno'}\n\n"
        f"Foco en mercado chileno. Resultado profesional y listo para usar."
    )

    result = await ollama_chat([{"role": "user", "content": prompt}])
    content = result.get("content", "")
    log_activity(f"Contenido generado: {content_type} — {topic[:50]}", level="success", task_id=task_id)
    return content or "(Sin contenido generado)"


async def _generate_file(
    title: str, content: str, fmt: str, subtitle: str, task_id: Optional[str]
) -> str:
    """Genera un archivo en el formato solicitado."""
    from files_engine.generator import generate_file

    log_activity(f"Generando archivo {fmt.upper()}: {title}", task_id=task_id)
    result = generate_file(title=title, content=content, file_format=fmt, subtitle=subtitle)

    if result.get("ok"):
        msg = (
            f"Archivo generado exitosamente:\n"
            f"  Nombre: {result['filename']}\n"
            f"  Formato: {result['format'].upper()}\n"
            f"  Tamaño: {result['size_kb']} KB\n"
            f"  Descarga: {result['download_url']}\n"
        )
        log_activity(f"Archivo generado: {result['filename']}", level="success", task_id=task_id)
    else:
        msg = f"Error generando archivo: {result.get('error', 'desconocido')}"
        log_activity(msg, level="error", task_id=task_id)
    return msg


async def _analyze_leads(action: str, task_id: Optional[str]) -> str:
    from leads_engine.storage import get_leads, get_stats

    log_activity(f"Analizando leads: {action}", task_id=task_id)
    stats = get_stats()
    leads = get_leads()

    now_str = datetime.now(TZ).strftime("%d/%m/%Y %H:%M hrs")
    out = [
        f"=== ANÁLISIS CRM — BAKU AGENCY ({now_str}) ===",
        f"Total leads: {stats.get('total', 0)}",
    ]
    for status, count in stats.get("by_status", {}).items():
        out.append(f"  {status}: {count}")

    if action == "identify_at_risk":
        new_leads = [l for l in leads if l.status == "new"]
        out.append(f"\nLeads sin contactar (riesgo enfriamiento): {len(new_leads)}")
        if new_leads:
            out.append("ACCIÓN RECOMENDADA: Contactar en las próximas 24h.")
            for l in new_leads[:5]:
                out.append(f"  • {l.full_name} — {l.source} — {l.created_at[:10]}")

    elif action == "suggest_followup":
        contacted = [l for l in leads if l.status == "contacted"]
        out.append(f"\nEn seguimiento activo: {len(contacted)}")
        out.append("ACCIÓN: Evaluar calificación y avanzar etapa.")

    elif action == "conversion_audit":
        converted = [l for l in leads if l.status == "converted"]
        total = stats.get("total", 1) or 1
        rate = round(len(converted) / total * 100, 1)
        out.append(f"\nTasa de conversión: {rate}%")
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
    now_str = datetime.now(TZ).strftime("%d/%m/%Y %H:%M hrs (Santiago)")
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

    lines += ["", "── ACTIVIDAD RECIENTE ──"]
    for a in recent_log[:5]:
        lines.append(f"  [{a['level'].upper()}] {a['message']}")

    if stats.get("total", 0) == 0:
        lines += [
            "",
            "── ALERTA CRÍTICA ──",
            "Sin leads registrados. Prioridad máxima: activar captación.",
            "Recomendación: Meta Ads test con presupuesto mínimo ($5.000 CLP/día).",
        ]

    if memory:
        lines += ["", "── CONTEXTO ESTRATÉGICO ──"]
        for k, v in list(memory.items())[:3]:
            lines.append(f"  {k}: {v}")

    report = "\n".join(lines)
    log_activity(f"Reporte {scope} generado", level="success", task_id=task_id)
    return report
