"""
BAKU Gateway Server — servidor WebSocket interno (reemplaza OpenClaw)

Corre en ws://127.0.0.1:18789 (el mismo puerto que OpenClaw Gateway esperaba).

Flujo:
  1. bridge.py se conecta y registra a BAKU_MASTER como agente.
  2. Clientes externos (otras apps, scripts) se conectan y chatean.
  3. El servidor rutea mensajes: cliente → agente → cliente.
  4. El gateway status en la UI se pone en verde.
"""

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import websockets
from websockets.server import WebSocketServerProtocol

log = logging.getLogger("baku.gateway.server")

GATEWAY_PORT     = int(os.getenv("GATEWAY_PORT", "18789"))
GATEWAY_AGENT_ID = os.getenv("GATEWAY_AGENT_ID", "main")

# ── Estado compartido ─────────────────────────────────────────────────────────
_agents: dict[str, WebSocketServerProtocol] = {}   # agent_id → ws
_pending: dict[str, asyncio.Future]          = {}   # request_id → future
_server_state = {
    "running":        False,
    "port":           GATEWAY_PORT,
    "agents":         [],
    "clients_served": 0,
    "started_at":     None,
}


def get_server_state() -> dict:
    return dict(
        _server_state,
        agents=list(_agents.keys()),
        pending_requests=len(_pending),
    )


# ── Enviar un mensaje al agente y esperar respuesta ───────────────────────────

async def _route_to_agent(
    agent_id: str,
    user_content: str,
    session_key: str,
    timeout: float = 120.0,
) -> str:
    ws = _agents.get(agent_id)
    if not ws:
        raise RuntimeError(f"Agente '{agent_id}' no conectado al Gateway")

    req_id = str(uuid.uuid4())[:12]
    loop   = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    _pending[req_id] = fut

    await ws.send(json.dumps({
        "id":   req_id,
        "type": "agent.send",
        "params": {
            "sessionKey": session_key,
            "message":    {"content": user_content},
        },
    }))

    try:
        result = await asyncio.wait_for(fut, timeout=timeout)
        return result.get("message", {}).get("content", "")
    except asyncio.TimeoutError:
        _pending.pop(req_id, None)
        raise RuntimeError("Timeout: el agente no respondió a tiempo")
    finally:
        _pending.pop(req_id, None)


# ── Manejador de conexión de AGENTE (bridge.py) ───────────────────────────────

async def _handle_agent(ws: WebSocketServerProtocol, agent_id: str) -> None:
    """Mantiene la conexión del agente y rutea sus respuestas."""
    _agents[agent_id] = ws
    _server_state["agents"] = list(_agents.keys())
    log.info("✅ Agente registrado: '%s'", agent_id)

    # Confirmar registro al bridge.py
    await ws.send(json.dumps({
        "type":    "agent.registered",
        "agentId": agent_id,
    }))

    try:
        async for raw in ws:
            if isinstance(raw, bytes):
                raw = raw.decode()
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                continue

            req_id = frame.get("id", "")
            result = frame.get("result")

            # Es la respuesta del agente a una solicitud pendiente
            if req_id and result is not None and req_id in _pending:
                fut = _pending.pop(req_id, None)
                if fut and not fut.done():
                    fut.set_result(result)

            # Ping del agente
            elif frame.get("type") == "ping":
                await ws.send(json.dumps({"type": "pong"}))

    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        if _agents.get(agent_id) is ws:
            del _agents[agent_id]
            _server_state["agents"] = list(_agents.keys())
            log.warning("⚠️  Agente desconectado: '%s'", agent_id)


# ── Manejador de conexión de CLIENTE (app externa) ────────────────────────────

async def _handle_client(
    ws: WebSocketServerProtocol,
    first_frame: dict,
) -> None:
    """Procesa chat desde un cliente externo, ruteando al agente disponible."""
    session_key = str(uuid.uuid4())[:8]
    log.info("🌐 Cliente externo conectado (session=%s)", session_key)
    _server_state["clients_served"] += 1

    # Procesar el primer frame ya leído antes de entrar aquí
    frames_to_process = [first_frame]

    async def _process(frame: dict) -> None:
        f_type   = frame.get("type", "")
        f_id     = frame.get("id", "")
        content  = (
            frame.get("content")
            or frame.get("text")
            or (frame.get("params") or {}).get("message", {}).get("content", "")
            or (frame.get("message") or {}).get("content", "")
        )

        if f_type in ("chat", "message", "sessions.send", "agent.message") or content:
            if not content:
                await ws.send(json.dumps({"id": f_id, "error": "Contenido vacío"}))
                return

            # Elegir el agente disponible (preferimos el configurado)
            target = GATEWAY_AGENT_ID if GATEWAY_AGENT_ID in _agents else next(iter(_agents), None)
            if not target:
                await ws.send(json.dumps({
                    "id":    f_id,
                    "error": "No hay agentes conectados al Gateway",
                }))
                return

            log.info("📨 [%s] → '%s...'", session_key, content[:60])
            try:
                reply = await _route_to_agent(target, content, session_key)
            except RuntimeError as e:
                await ws.send(json.dumps({"id": f_id, "error": str(e)}))
                return

            await ws.send(json.dumps({
                "id":     f_id,
                "type":   "message",
                "result": {"message": {"role": "assistant", "content": reply}},
                "content": reply,
            }))
            log.info("📤 [%s] ← %d chars", session_key, len(reply))

        elif f_type == "ping":
            await ws.send(json.dumps({"type": "pong", "id": f_id}))

    for f in frames_to_process:
        await _process(f)

    try:
        async for raw in ws:
            if isinstance(raw, bytes):
                raw = raw.decode()
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                continue
            await _process(frame)
    except websockets.exceptions.ConnectionClosed:
        pass

    log.info("🌐 Cliente desconectado (session=%s)", session_key)


# ── Dispatcher principal de conexiones ───────────────────────────────────────

async def _handle_connection(ws: WebSocketServerProtocol) -> None:
    """
    Distingue si la conexión es un AGENTE o un CLIENTE externo
    basándose en el primer mensaje recibido.
    """
    # Enviar greeting para que bridge.py sepa que debe registrarse
    await ws.send(json.dumps({
        "type":    "hello",
        "gateway": "BAKU-Gateway",
        "version": "1.0",
    }))

    try:
        raw = await asyncio.wait_for(ws.recv(), timeout=8.0)
    except asyncio.TimeoutError:
        log.debug("Conexión sin primer mensaje — cerrando")
        return
    except websockets.exceptions.ConnectionClosed:
        return

    if isinstance(raw, bytes):
        raw = raw.decode()

    try:
        frame = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("Primer frame no es JSON válido")
        return

    f_type   = frame.get("type", "")
    agent_id = frame.get("agentId") or frame.get("agent_id")

    # Conexión de AGENTE (bridge.py)
    if f_type in ("auth", "register", "agent.register") and agent_id:
        await _handle_agent(ws, agent_id)
    else:
        # Conexión de CLIENTE externo — procesar el primer frame también
        await _handle_client(ws, frame)


# ── Ciclo del servidor ────────────────────────────────────────────────────────

_server_task: Optional[asyncio.Task] = None


async def start_gateway_server() -> None:
    global _server_task
    _server_task = asyncio.create_task(_run_server(), name="baku-gateway-server")
    log.info("Gateway Server iniciado en ws://0.0.0.0:%d", GATEWAY_PORT)


async def stop_gateway_server() -> None:
    global _server_task
    if _server_task and not _server_task.done():
        _server_task.cancel()
        try:
            await asyncio.wait_for(_server_task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    _server_state["running"] = False
    log.info("Gateway Server detenido.")


async def _run_server() -> None:
    _server_state["running"]    = True
    _server_state["started_at"] = datetime.now(timezone.utc).isoformat()

    async with websockets.serve(
        _handle_connection,
        "0.0.0.0",
        GATEWAY_PORT,
        ping_interval=30,
        ping_timeout=10,
    ):
        log.info("🚀 BAKU Gateway escuchando en ws://0.0.0.0:%d", GATEWAY_PORT)
        await asyncio.Future()  # corre indefinidamente
