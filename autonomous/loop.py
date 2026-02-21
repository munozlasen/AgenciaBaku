"""
BAKU_MASTER — Bucle autónomo principal.

Cada ciclo:
  1. Revisa la cola de tareas pendientes.
  2. Si hay tareas → ejecuta la de mayor prioridad usando Ollama con tool-calling.
  3. Si no hay tareas → decide proactivamente qué hacer para avanzar la agencia.
  4. Registra todo en activity_log.

Ollama tool-calling loop (máximo MAX_TOOL_ROUNDS rondas por tarea):
  assistant llama tool → ejecutamos → devolvemos resultado → assistant decide si sigue o termina.
"""

import json
import os
import uuid
from pathlib import Path
from typing import Optional

import httpx

from autonomous.memory import (
    create_task,
    get_all_memory,
    get_pending_tasks,
    get_recent_activity,
    log_activity,
    update_task_status,
)
from autonomous.tools import TOOLS_SPEC, execute_tool

OLLAMA_HOST        = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_MODEL       = os.getenv("OLLAMA_MODEL", "qwen3:8b")
OLLAMA_NUM_CTX     = int(os.getenv("OLLAMA_NUM_CTX", "16384"))
OLLAMA_TEMPERATURE = float(os.getenv("OLLAMA_TEMPERATURE", "0.7"))
SOUL_PATH = Path(__file__).parent.parent / "SOUL.md"

MAX_TOOL_ROUNDS = 5  # máximo de rondas de tool-calling por tarea


# ── Soul loader ────────────────────────────────────────────────────────────────

def _load_soul() -> str:
    if SOUL_PATH.exists():
        return SOUL_PATH.read_text(encoding="utf-8")
    return (
        "Eres BAKU_MASTER, Director Estratégico Senior de BAKU Agency Chile. "
        "Opera con criterio profesional, visión sistémica y foco en resultados."
    )


def _system_prompt() -> str:
    soul = _load_soul()
    return (
        soul
        + "\n\n"
        "====================================================\n"
        "MODO AUTÓNOMO ACTIVO\n"
        "====================================================\n"
        "Tienes herramientas disponibles. Úsalas de forma proactiva.\n"
        "Razona brevemente, luego actúa. Prioriza resultados concretos.\n"
        "Puedes encadenar múltiples herramientas en un ciclo.\n"
        "Cuando termines, entrega un resumen ejecutivo de lo realizado.\n"
    )


# ── Ollama caller ──────────────────────────────────────────────────────────────

async def _call_ollama(messages: list[dict]) -> dict:
    """Call Ollama API and return the message dict."""
    async with httpx.AsyncClient(timeout=300.0) as client:
        r = await client.post(
            f"{OLLAMA_HOST}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": messages,
                "tools": TOOLS_SPEC,
                "stream": False,
                "options": {
                    "num_ctx": OLLAMA_NUM_CTX,
                    "temperature": OLLAMA_TEMPERATURE,
                },
            },
        )
        r.raise_for_status()
        return r.json()["message"]


# ── Main cycle ─────────────────────────────────────────────────────────────────

async def run_agent_cycle() -> None:
    """One full autonomous cycle."""
    cycle_id = str(uuid.uuid4())[:8]
    log_activity(f"[{cycle_id}] ── Ciclo autónomo iniciado ──", level="info")

    pending = get_pending_tasks(limit=3)

    if pending:
        task = pending[0]
        await _execute_task(task, cycle_id)
    else:
        await _proactive_cycle(cycle_id)

    log_activity(f"[{cycle_id}] ── Ciclo completado ──", level="info")


# ── Task executor ──────────────────────────────────────────────────────────────

async def _execute_task(task: dict, cycle_id: str) -> None:
    """Execute a queued task using Ollama with tool-calling."""
    task_id = task["id"]
    log_activity(
        f"[{cycle_id}] Ejecutando: {task['title']}",
        level="info",
        task_id=task_id,
    )
    update_task_status(task_id, "running")

    messages = [
        {"role": "system", "content": _system_prompt()},
        {
            "role": "user",
            "content": (
                f"Tarea asignada:\n"
                f"Título: {task['title']}\n"
                f"Descripción: {task.get('description', '')}\n"
                f"Tipo: {task.get('type', 'general')}\n\n"
                f"Ejecuta esta tarea completamente. Usa las herramientas necesarias. "
                f"Al finalizar, entrega un resumen ejecutivo de los resultados."
            ),
        },
    ]

    try:
        result = await _tool_calling_loop(messages, task_id, cycle_id)
        update_task_status(task_id, "done", result=result[:1000])
        log_activity(
            f"[{cycle_id}] Tarea completada: {task['title']}",
            level="success",
            task_id=task_id,
        )
    except httpx.ConnectError:
        error = "Ollama no disponible — verifica que esté corriendo en http://127.0.0.1:11434"
        update_task_status(task_id, "failed", error=error)
        log_activity(f"[{cycle_id}] {error}", level="error", task_id=task_id)
    except Exception as e:
        update_task_status(task_id, "failed", error=str(e))
        log_activity(f"[{cycle_id}] Error: {e}", level="error", task_id=task_id)


# ── Proactive decision cycle ───────────────────────────────────────────────────

async def _proactive_cycle(cycle_id: str) -> None:
    """When queue is empty, agent decides what to do proactively."""
    from leads_engine.storage import get_stats

    stats = get_stats()
    recent = get_recent_activity(limit=8)
    memory = get_all_memory()

    recent_text = "\n".join(
        f"  [{a['level'].upper()}] {a['message']}" for a in recent
    ) or "  (Sin actividad reciente)"

    memory_text = (
        "\n".join(f"  {k}: {v['value']}" for k, v in list(memory.items())[:5])
        or "  (Sin datos guardados)"
    )

    context_msg = (
        f"Estado actual de BAKU Agency:\n"
        f"  Leads totales: {stats.get('total', 0)}\n"
        f"  Por estado: {json.dumps(stats.get('by_status', {}), ensure_ascii=False)}\n\n"
        f"Actividad reciente del agente:\n{recent_text}\n\n"
        f"Memoria estratégica:\n{memory_text}\n\n"
        f"No hay tareas pendientes en la cola.\n"
        f"Como Director Estratégico, decide qué es lo más urgente para hacer avanzar la agencia ahora mismo.\n"
        f"Crea 2-3 tareas estratégicas usando create_strategic_task y ejecuta al menos una acción inmediata."
    )

    messages = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": context_msg},
    ]

    log_activity(f"[{cycle_id}] Modo proactivo — decidiendo próximas acciones", level="info")

    try:
        result = await _tool_calling_loop(messages, task_id=None, cycle_id=cycle_id)
        log_activity(
            f"[{cycle_id}] Ciclo proactivo completado: {result[:200]}",
            level="success",
        )
    except httpx.ConnectError:
        log_activity(
            f"[{cycle_id}] Ollama no disponible — ciclo proactivo omitido",
            level="warning",
        )
    except Exception as e:
        log_activity(f"[{cycle_id}] Error en ciclo proactivo: {e}", level="error")


# ── Tool-calling loop ──────────────────────────────────────────────────────────

async def _tool_calling_loop(
    messages: list[dict],
    task_id: Optional[str],
    cycle_id: str,
) -> str:
    """
    Iterates: assistant → tool calls → tool results → assistant → ...
    Returns the final text content from the assistant.
    """
    for round_num in range(MAX_TOOL_ROUNDS):
        msg = await _call_ollama(messages)
        tool_calls = msg.get("tool_calls", [])

        if not tool_calls:
            # Assistant gave a final answer — done
            return msg.get("content", "(Sin respuesta final)")

        # Append assistant message with tool_calls
        messages.append({
            "role": "assistant",
            "content": msg.get("content", ""),
            "tool_calls": tool_calls,
        })

        # Execute each tool and append results
        for tc in tool_calls:
            fn = tc.get("function", {})
            tool_name = fn.get("name", "")
            args = fn.get("arguments", {})

            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}

            log_activity(
                f"[{cycle_id}] Tool: {tool_name}({list(args.keys())})",
                task_id=task_id,
            )
            tool_result = await execute_tool(tool_name, args, task_id=task_id)

            messages.append({
                "role": "tool",
                "content": tool_result,
                "name": tool_name,
            })

    # Exhausted max rounds — ask for a summary
    messages.append({
        "role": "user",
        "content": "Entrega el resumen ejecutivo final de todo lo realizado.",
    })
    msg = await _call_ollama(messages)
    return msg.get("content", "(Máximo de rondas alcanzado sin respuesta final)")
