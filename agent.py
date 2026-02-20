"""
BAKU_MASTER — Conector Ollama.

Carga SOUL.md, inyecta contexto de hora/fecha en Santiago de Chile y
llama a Ollama con la lista de mensajes.

Config via .env:
  OLLAMA_HOST   default: http://127.0.0.1:11434
  OLLAMA_MODEL  default: qwen3:1.7b
  OPENCLAW_SOUL ruta a SOUL.md
"""

import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv

load_dotenv()

OLLAMA_HOST  = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:1.7b")
SOUL_PATH    = Path(os.getenv(
    "OPENCLAW_SOUL",
    r"C:\OpenClawWorkspace\.openclaw\workspace\SOUL.md"
))

TZ_SANTIAGO = ZoneInfo("America/Santiago")

_FALLBACK_SOUL = (
    "Eres BAKU_MASTER, el cerebro estratégico de BAKU Agency. "
    "Siempre respondes en español chileno, de forma directa y natural. "
    "Nunca usas inglés. Nunca hablas como robot."
)

_LEADS_CONTEXT = """

## SISTEMA DE LEADS
Tienes un CRM conectado en http://localhost:8000.
Endpoints disponibles:
  POST /leads              - registrar nuevo lead
  GET  /leads              - listar leads (?status=new|contacted|qualified|converted|discarded)
  PATCH /leads/{id}/status - actualizar estado de un lead
  GET  /stats              - conteos por estado y fuente

## SISTEMA DE ARCHIVOS
Puedes generar archivos para el usuario usando la tool `generate_file`:
  - DOCX: propuestas, reportes, briefs (se abre en Word)
  - XLSX: modelos financieros, planillas (se abre en Excel)
  - PDF: documentos para clientes, reportes ejecutivos
  - TXT / MD: notas, borradores, prompts

Los archivos generados quedan disponibles para descarga en /files.

## BÚSQUEDA WEB
Tienes búsqueda web en tiempo real. Si necesitas datos actuales del mercado chileno,
competencia, precios o tendencias, usa la tool `web_search`.
"""


def _runtime_context() -> str:
    """Genera el bloque de contexto en tiempo real — hora Santiago."""
    now = datetime.now(TZ_SANTIAGO)
    dia_semana = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    mes = [
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
    ]
    return (
        f"\n\n## CONTEXTO OPERATIVO EN TIEMPO REAL\n"
        f"Fecha y hora actual (Santiago, Chile): "
        f"{dia_semana[now.weekday()]} {now.day} de {mes[now.month - 1]} de {now.year}, "
        f"{now.strftime('%H:%M')} hrs\n"
        f"Zona horaria: America/Santiago (UTC{now.strftime('%z')})\n"
    )


def _load_soul() -> str:
    """Carga SOUL.md y agrega contexto de tiempo real."""
    if SOUL_PATH.exists():
        raw = SOUL_PATH.read_text(encoding="utf-8")
    else:
        # Fallback al SOUL.md local del proyecto
        local = Path(__file__).parent / "SOUL.md"
        raw = local.read_text(encoding="utf-8") if local.exists() else _FALLBACK_SOUL

    # Reemplazar el placeholder de contexto o agregarlo al final
    marker = "<!-- RUNTIME_CONTEXT — esto es reemplazado dinámicamente al inicio de cada conversación -->"
    ctx = _runtime_context() + _LEADS_CONTEXT
    if marker in raw:
        return raw.replace(marker, ctx)
    return raw + ctx


def _strip_think(text: str) -> tuple[str, str]:
    """Extrae bloque <think>...</think> si existe."""
    match = re.search(r"<think>(.*?)</think>", text, re.DOTALL)
    thinking = match.group(1).strip() if match else ""
    clean = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    if not clean and thinking:
        clean = thinking
        thinking = ""
    return clean, thinking


def _detect_mode(text: str) -> str:
    """Detecta el modo operacional activo en la respuesta."""
    for mode in [
        "CEO_MODE", "CFO_MODE", "META_ADS_MODE", "GOOGLE_ADS_MODE",
        "INFLUENCER_MODE", "CONTENT_ENGINE_MODE", "LEADS_ENGINE_MODE",
        "OPTIMIZER_MODE", "PERFORMANCE_MONITOR", "CFO_AUDIT", "CONTENT_AUTOMATION",
    ]:
        if mode in text:
            return mode
    return "BAKU_MASTER"


async def chat(messages: list[dict]) -> dict:
    """Envía mensajes a BAKU_MASTER vía Ollama native API."""
    full_messages = [{"role": "system", "content": _load_soul()}] + messages

    try:
        async with httpx.AsyncClient(timeout=600.0) as client:
            r = await client.post(
                f"{OLLAMA_HOST}/api/chat",
                json={
                    "model": OLLAMA_MODEL,
                    "messages": full_messages,
                    "stream": False,
                },
            )
            r.raise_for_status()
            data = r.json()
            raw_content = data["message"].get("content", "").strip()
            thinking_field = data["message"].get("thinking", "")

            # Qwen3 puede meter el thinking dentro del content o en campo separado
            if "<think>" in raw_content:
                content, thinking = _strip_think(raw_content)
            else:
                content = raw_content
                thinking = thinking_field

            if not content and thinking:
                content = thinking
                thinking = ""

            return {
                "content": content,
                "thinking": thinking,
                "mode": _detect_mode(content),
                "via": "ollama",
                "error": None,
            }

    except httpx.ConnectError:
        return {
            "content": "",
            "thinking": "",
            "mode": "",
            "via": "",
            "error": "No se puede conectar a Ollama. Verifica que esté corriendo en http://127.0.0.1:11434",
        }
    except Exception as e:
        return {"content": "", "thinking": "", "mode": "", "via": "", "error": str(e)}
