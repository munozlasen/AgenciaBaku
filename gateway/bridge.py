"""
OpenClaw Gateway Bridge — BAKU_MASTER

Conecta AgenciaBaku al OpenClaw Gateway vía WebSocket.
Registra BAKU_MASTER como el agente principal ("main") del Gateway,
permitiendo que mensajes de cualquier canal (WhatsApp, Telegram, Discord,
UI local, etc.) sean procesados por el motor de BAKU_MASTER (Ollama).

Config via .env:
  GATEWAY_WS_URL    URL WebSocket del Gateway  (default: ws://127.0.0.1:18789)
  GATEWAY_TOKEN     Token de autenticación     (opcional)
  GATEWAY_AGENT_ID  ID del agente registrado   (default: main)
"""

import asyncio
import json
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

import websockets
from dotenv import load_dotenv

from agent import chat

load_dotenv()

log = logging.getLogger("baku.gateway")

GATEWAY_WS_URL = os.getenv("GATEWAY_WS_URL", "ws://127.0.0.1:18789")
GATEWAY_TOKEN = os.getenv("GATEWAY_TOKEN", "")
GATEWAY_AGENT_ID = os.getenv("GATEWAY_AGENT_ID", "main")

# ── Estado global del bridge (consultado por /gateway/status) ───────────────
_state: dict = {
    "connected": False,
    "agent_id": GATEWAY_AGENT_ID,
    "gateway_url": GATEWAY_WS_URL,
    "sessions": 0,
    "messages_received": 0,
    "messages_sent": 0,
    "last_message_at": None,
    "last_error": None,
    "uptime_since": None,
    "reconnect_attempts": 0,
}

# Historial de mensajes por session_key (para contexto multi-turno)
_session_histories: dict[str, list[dict]] = defaultdict(list)

# Task de asyncio del bridge (para control de ciclo de vida)
_bridge_task: Optional[asyncio.Task] = None


def get_status() -> dict:
    """Retorna el estado actual del bridge (para el endpoint /gateway/status)."""
    return dict(_state)


# ── Protocolo OpenClaw ───────────────────────────────────────────────────────

def _build_connect_frame() -> str:
    """Frame inicial de handshake con el Gateway."""
    payload: dict = {
        "type": "connect",
        "params": {
            "minProtocol": 1,
            "maxProtocol": 2,
            "role": "agent",
            "agentId": GATEWAY_AGENT_ID,
        },
    }
    if GATEWAY_TOKEN:
        payload["params"]["auth"] = {"token": GATEWAY_TOKEN}
    return json.dumps(payload)


def _build_response(request_id: str, result: dict) -> str:
    """Respuesta RPC estándar para una petición del Gateway."""
    return json.dumps({"id": request_id, "result": result})


def _build_error_response(request_id: str, message: str, code: int = -32000) -> str:
    """Respuesta de error RPC."""
    return json.dumps({
        "id": request_id,
        "error": {"code": code, "message": message},
    })


def _extract_session_key(params: dict) -> str:
    """Extrae el session_key de los params del mensaje."""
    return (
        params.get("sessionKey")
        or params.get("session_key")
        or f"agent:{GATEWAY_AGENT_ID}:main"
    )


def _extract_user_content(params: dict) -> str:
    """Extrae el texto del usuario del frame del Gateway."""
    msg = params.get("message") or {}
    if isinstance(msg, dict):
        return msg.get("content") or msg.get("text") or ""
    if isinstance(msg, str):
        return msg
    # Algunos gateways envían el contenido directamente en params
    return params.get("content") or params.get("text") or ""


# ── Procesamiento de mensajes ────────────────────────────────────────────────

async def _handle_message(ws, raw: str) -> None:
    """Procesa un mensaje recibido del Gateway."""
    try:
        frame = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("Gateway envió JSON inválido: %s", raw[:200])
        return

    msg_type = frame.get("type", "")
    request_id = frame.get("id", "")
    params = frame.get("params") or {}

    _state["messages_received"] += 1
    _state["last_message_at"] = datetime.now(timezone.utc).isoformat()

    log.debug("Gateway → [%s] id=%s", msg_type, request_id)

    # ── Handshake confirmado ──────────────────────────────────────────────
    if msg_type in ("connected", "connect.ack", "welcome"):
        _state["connected"] = True
        _state["uptime_since"] = datetime.now(timezone.utc).isoformat()
        _state["reconnect_attempts"] = 0
        log.info("✅ Conectado al Gateway como agente '%s'", GATEWAY_AGENT_ID)
        return

    # ── Mensajes de chat / sesión ─────────────────────────────────────────
    if msg_type in ("sessions.send", "agent.send", "message", "chat"):
        session_key = _extract_session_key(params)
        user_content = _extract_user_content(params)

        if not user_content:
            log.warning("Mensaje sin contenido para sesión %s", session_key)
            if request_id:
                await ws.send(_build_error_response(request_id, "Contenido vacío"))
            return

        log.info("📨 [%s] → '%s...'", session_key, user_content[:60])

        # Agregar al historial de sesión
        history = _session_histories[session_key]
        history.append({"role": "user", "content": user_content})

        # Mantener ventana de contexto (últimos 20 mensajes)
        if len(history) > 20:
            history[:] = history[-20:]

        # Procesar con BAKU_MASTER
        result = await chat(history)

        if result.get("error"):
            log.error("Error de BAKU_MASTER: %s", result["error"])
            if request_id:
                await ws.send(_build_error_response(request_id, result["error"]))
            return

        assistant_content = result.get("content", "")
        history.append({"role": "assistant", "content": assistant_content})

        # Contar sesiones únicas activas
        _state["sessions"] = len(_session_histories)

        # Responder al Gateway
        response = _build_response(request_id, {
            "message": {
                "role": "assistant",
                "content": assistant_content,
            },
            "meta": {
                "mode": result.get("mode", "BAKU_MASTER"),
                "via": result.get("via", "ollama"),
                "agent": GATEWAY_AGENT_ID,
            },
        })
        await ws.send(response)
        _state["messages_sent"] += 1
        log.info("📤 [%s] ← %d chars (mode: %s)", session_key, len(assistant_content), result.get("mode"))
        return

    # ── Limpiar sesión ────────────────────────────────────────────────────
    if msg_type in ("sessions.clear", "session.reset"):
        session_key = _extract_session_key(params)
        _session_histories.pop(session_key, None)
        _state["sessions"] = len(_session_histories)
        log.info("🗑️  Sesión limpiada: %s", session_key)
        if request_id:
            await ws.send(_build_response(request_id, {"cleared": True}))
        return

    # ── Ping / heartbeat ──────────────────────────────────────────────────
    if msg_type in ("ping", "heartbeat"):
        if request_id:
            await ws.send(_build_response(request_id, {"pong": True}))
        return

    # ── Config / capabilities (el Gateway puede consultar al agente) ──────
    if msg_type == "agent.capabilities":
        await ws.send(_build_response(request_id, {
            "agentId": GATEWAY_AGENT_ID,
            "name": "BAKU_MASTER",
            "description": "Director Estratégico Senior de Baku Agency. Meta Ads, Google Ads, Leads, CRO.",
            "capabilities": [
                "chat", "leads_management", "autonomous_scheduler",
                "strategic_analysis", "content_generation",
            ],
        }))
        return

    # ── Frame desconocido ─────────────────────────────────────────────────
    log.debug("Frame no manejado: type=%s", msg_type)


# ── Ciclo de conexión con reconexión automática ──────────────────────────────

async def _run_bridge() -> None:
    """Bucle principal del bridge con reconexión exponencial."""
    backoff = 2
    max_backoff = 60

    while True:
        try:
            log.info("🔌 Conectando al Gateway: %s", GATEWAY_WS_URL)
            _state["connected"] = False

            async with websockets.connect(
                GATEWAY_WS_URL,
                open_timeout=10,
                ping_interval=30,
                ping_timeout=10,
            ) as ws:
                # Handshake inicial
                await ws.send(_build_connect_frame())
                backoff = 2  # Reset backoff al conectar

                async for raw in ws:
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    await _handle_message(ws, raw)

        except asyncio.CancelledError:
            log.info("Bridge detenido.")
            _state["connected"] = False
            break

        except (websockets.exceptions.ConnectionClosed,
                websockets.exceptions.WebSocketException,
                OSError) as e:
            _state["connected"] = False
            _state["last_error"] = str(e)
            _state["reconnect_attempts"] += 1
            log.warning(
                "Gateway desconectado (%s). Reconectando en %ds... (intento %d)",
                e, backoff, _state["reconnect_attempts"]
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

        except Exception as e:
            _state["connected"] = False
            _state["last_error"] = str(e)
            log.error("Error inesperado en el bridge: %s", e, exc_info=True)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)


# ── API pública (llamada desde api.py en el lifespan) ───────────────────────

async def start_bridge() -> None:
    """Inicia el bridge en background. Llamar desde el startup de FastAPI."""
    global _bridge_task
    _bridge_task = asyncio.create_task(_run_bridge(), name="openclaw-bridge")
    log.info("Gateway bridge iniciado → %s (agent: %s)", GATEWAY_WS_URL, GATEWAY_AGENT_ID)


async def stop_bridge() -> None:
    """Detiene el bridge. Llamar desde el shutdown de FastAPI."""
    global _bridge_task
    if _bridge_task and not _bridge_task.done():
        _bridge_task.cancel()
        try:
            await asyncio.wait_for(_bridge_task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    _state["connected"] = False
    log.info("Gateway bridge detenido.")
