"""
OpenClaw Gateway Bridge — BAKU_MASTER

Conecta AgenciaBaku al OpenClaw Gateway vía WebSocket.
BAKU_MASTER se registra como el agente principal ("main") del Gateway.

Flujo correcto de OpenClaw:
  1. Cliente se conecta al WebSocket
  2. Gateway envía su primer frame (greeting / challenge)
  3. Cliente responde con credenciales / confirmación
  4. Gateway rutea mensajes → cliente procesa → responde

Config via .env:
  GATEWAY_WS_URL    URL WebSocket del Gateway  (default: ws://127.0.0.1:18789)
  GATEWAY_TOKEN     Token de autenticación     (opcional)
  GATEWAY_AGENT_ID  ID del agente registrado   (default: main)
"""

import asyncio
import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

import websockets
from dotenv import load_dotenv

from agent import chat

load_dotenv()

log = logging.getLogger("baku.gateway")

GATEWAY_WS_URL  = os.getenv("GATEWAY_WS_URL", "ws://127.0.0.1:18789")
GATEWAY_TOKEN   = os.getenv("GATEWAY_TOKEN", "")
GATEWAY_AGENT_ID = os.getenv("GATEWAY_AGENT_ID", "main")

# ── Estado global (consultado por /gateway/status) ───────────────────────────
_state: dict = {
    "connected":        False,
    "agent_id":         GATEWAY_AGENT_ID,
    "gateway_url":      GATEWAY_WS_URL,
    "sessions":         0,
    "messages_received":0,
    "messages_sent":    0,
    "last_message_at":  None,
    "last_error":       None,
    "uptime_since":     None,
    "reconnect_attempts":0,
    "probe_log":        [],   # últimos frames crudos recibidos (para debugging)
}

_session_histories: dict[str, list[dict]] = defaultdict(list)
_bridge_task: Optional[asyncio.Task] = None

# Si el Gateway devuelve 1008 consecutivos, aumentamos el cooldown
_consecutive_policy_errors = 0
_MAX_POLICY_ERRORS = 5          # después de 5 rechazos → modo standby largo
_STANDBY_SECS      = 300        # 5 minutos en standby


def get_status() -> dict:
    return dict(_state)


# ── Helpers de protocolo ─────────────────────────────────────────────────────

def _build_auth_reply(server_frame: dict) -> str:
    """
    Construye la respuesta al greeting del Gateway.
    OpenClaw puede enviar distintos formatos; cubrimos los más comunes.
    """
    req_id = server_frame.get("id", "")
    s_type = server_frame.get("type", "")

    # Formato 1: el Gateway pide autenticación explícita
    if s_type in ("auth.required", "challenge", "hello"):
        payload: dict = {
            "type": "auth",
            "agentId": GATEWAY_AGENT_ID,
        }
        if req_id:
            payload["id"] = req_id
        if GATEWAY_TOKEN:
            payload["token"] = GATEWAY_TOKEN
        return json.dumps(payload)

    # Formato 2: el Gateway envía un "hello" y espera un "register"
    if s_type in ("server.hello", "gateway.hello", "welcome"):
        payload = {
            "type": "register",
            "agentId": GATEWAY_AGENT_ID,
            "role": "agent",
        }
        if GATEWAY_TOKEN:
            payload["token"] = GATEWAY_TOKEN
        return json.dumps(payload)

    # Formato 3: RPC — el Gateway envía una petición con id y espera respuesta
    if req_id and s_type == "connect":
        return json.dumps({
            "id": req_id,
            "result": {
                "agentId": GATEWAY_AGENT_ID,
                **({"token": GATEWAY_TOKEN} if GATEWAY_TOKEN else {}),
            },
        })

    # Fallback: envía un frame de registro genérico
    return json.dumps({
        "type": "agent.register",
        "agentId": GATEWAY_AGENT_ID,
        **({"token": GATEWAY_TOKEN} if GATEWAY_TOKEN else {}),
    })


def _build_rpc_response(request_id: str, result: dict) -> str:
    return json.dumps({"id": request_id, "result": result})


def _build_rpc_error(request_id: str, message: str) -> str:
    return json.dumps({"id": request_id, "error": {"code": -32000, "message": message}})


def _extract_session_key(params: dict) -> str:
    return (
        params.get("sessionKey")
        or params.get("session_key")
        or f"agent:{GATEWAY_AGENT_ID}:main"
    )


def _extract_user_content(params: dict) -> str:
    msg = params.get("message") or {}
    if isinstance(msg, dict):
        return msg.get("content") or msg.get("text") or ""
    if isinstance(msg, str):
        return msg
    return params.get("content") or params.get("text") or ""


# ── Procesamiento de mensajes ────────────────────────────────────────────────

async def _handle_frame(ws, raw: str, is_first: bool) -> bool:
    """
    Procesa un frame del Gateway.
    `is_first=True` → es el primer mensaje tras conectar (puede ser greeting).
    Retorna True si la conexión debe seguir abierta.
    """
    try:
        frame = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("Frame JSON inválido: %s", raw[:300])
        return True

    # Guardar en probe_log para diagnóstico (máx 10 entradas)
    _state["probe_log"] = ([raw[:500]] + _state["probe_log"])[:10]

    msg_type = frame.get("type", "")
    request_id = frame.get("id", "") or ""
    params = frame.get("params") or frame.get("data") or {}

    _state["messages_received"] += 1
    _state["last_message_at"] = datetime.now(timezone.utc).isoformat()

    log.debug("← Gateway [%s] id=%s", msg_type, request_id)

    # ── El Gateway envía su greeting primero ─────────────────────────────
    if is_first or msg_type in (
        "hello", "server.hello", "gateway.hello", "welcome",
        "auth.required", "challenge", "connect",
    ):
        log.info("🤝 Gateway greeting recibido (type=%s) — respondiendo...", msg_type or "?")
        reply = _build_auth_reply(frame)
        await ws.send(reply)
        log.debug("→ Gateway auth/register enviado")
        return True

    # ── Handshake confirmado ──────────────────────────────────────────────
    if msg_type in ("connected", "connect.ack", "auth.ok", "registered", "agent.registered"):
        _state["connected"] = True
        _state["uptime_since"] = datetime.now(timezone.utc).isoformat()
        log.info("✅ BAKU_MASTER registrado en el Gateway como agente '%s'", GATEWAY_AGENT_ID)
        return True

    # ── Mensajes de chat / sesión ─────────────────────────────────────────
    if msg_type in ("sessions.send", "agent.send", "message", "chat", "agent.message"):
        session_key = _extract_session_key(params)
        user_content = _extract_user_content(params)

        if not user_content:
            if request_id:
                await ws.send(_build_rpc_error(request_id, "Contenido vacío"))
            return True

        log.info("📨 [%s] → '%s...'", session_key, user_content[:60])
        history = _session_histories[session_key]
        history.append({"role": "user", "content": user_content})
        if len(history) > 20:
            history[:] = history[-20:]

        result = await chat(history)
        if result.get("error"):
            log.error("Error BAKU_MASTER: %s", result["error"])
            if request_id:
                await ws.send(_build_rpc_error(request_id, result["error"]))
            return True

        content = result.get("content", "")
        history.append({"role": "assistant", "content": content})
        _state["sessions"] = len(_session_histories)

        await ws.send(_build_rpc_response(request_id, {
            "message": {"role": "assistant", "content": content},
            "meta": {"mode": result.get("mode", "BAKU_MASTER"), "agent": GATEWAY_AGENT_ID},
        }))
        _state["messages_sent"] += 1
        log.info("📤 [%s] ← %d chars", session_key, len(content))
        return True

    # ── Ping ──────────────────────────────────────────────────────────────
    if msg_type in ("ping", "heartbeat"):
        if request_id:
            await ws.send(_build_rpc_response(request_id, {"pong": True}))
        return True

    # ── Limpiar sesión ────────────────────────────────────────────────────
    if msg_type in ("sessions.clear", "session.reset"):
        sk = _extract_session_key(params)
        _session_histories.pop(sk, None)
        _state["sessions"] = len(_session_histories)
        if request_id:
            await ws.send(_build_rpc_response(request_id, {"cleared": True}))
        return True

    # ── Frame desconocido — loguear completo para diagnóstico ─────────────
    log.debug("Frame no manejado: %s", raw[:300])
    return True


# ── Ciclo principal ──────────────────────────────────────────────────────────

async def _run_bridge() -> None:
    global _consecutive_policy_errors
    backoff = 5
    max_backoff = 60

    while True:
        try:
            log.info("🔌 Conectando al Gateway: %s", GATEWAY_WS_URL)
            _state["connected"] = False

            # Token en query string (algunos gateways lo esperan aquí)
            url = GATEWAY_WS_URL
            if GATEWAY_TOKEN and "token=" not in url:
                sep = "&" if "?" in url else "?"
                url = f"{url}{sep}token={GATEWAY_TOKEN}&agentId={GATEWAY_AGENT_ID}"

            async with websockets.connect(
                url,
                open_timeout=10,
                ping_interval=None,   # desactivamos ping automático; el gateway lo maneja
                additional_headers={
                    "X-Agent-Id": GATEWAY_AGENT_ID,
                    **({"X-Agent-Token": GATEWAY_TOKEN} if GATEWAY_TOKEN else {}),
                },
            ) as ws:
                _consecutive_policy_errors = 0
                backoff = 5

                # --- Escuchar el greeting del Gateway (máx 5s) ---
                first_frame = True
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    if isinstance(raw, bytes):
                        raw = raw.decode()
                    await _handle_frame(ws, raw, is_first=True)
                    first_frame = False
                except asyncio.TimeoutError:
                    # Gateway no envió greeting — intentamos registrarnos nosotros
                    log.info("Sin greeting del Gateway — enviando registro proactivo...")
                    await ws.send(json.dumps({
                        "type": "agent.register",
                        "agentId": GATEWAY_AGENT_ID,
                        **({"token": GATEWAY_TOKEN} if GATEWAY_TOKEN else {}),
                    }))

                # --- Loop principal de mensajes ---
                async for raw in ws:
                    if isinstance(raw, bytes):
                        raw = raw.decode()
                    await _handle_frame(ws, raw, is_first=False)

        except asyncio.CancelledError:
            log.info("Bridge detenido.")
            _state["connected"] = False
            break

        except websockets.exceptions.ConnectionClosedError as e:
            _state["connected"] = False
            _state["last_error"] = str(e)
            _state["reconnect_attempts"] += 1

            # 1008 = policy violation (protocolo incorrecto o token inválido)
            if e.code == 1008:
                _consecutive_policy_errors += 1
                log.warning(
                    "⚠️  Gateway rechazó conexión (1008 policy violation) — "
                    "intento %d/%d. Revisa GATEWAY_TOKEN o el protocolo.",
                    _consecutive_policy_errors, _MAX_POLICY_ERRORS
                )
                if _consecutive_policy_errors >= _MAX_POLICY_ERRORS:
                    log.warning(
                        "🔴 %d rechazos consecutivos. Entrando en standby %ds. "
                        "Verifica la config del Gateway y GATEWAY_TOKEN en .env",
                        _consecutive_policy_errors, _STANDBY_SECS
                    )
                    _state["last_error"] = (
                        f"Gateway rechazó {_consecutive_policy_errors} veces (1008). "
                        "Revisa GATEWAY_TOKEN en .env."
                    )
                    await asyncio.sleep(_STANDBY_SECS)
                    _consecutive_policy_errors = 0
                    backoff = 5
                    continue
            else:
                log.warning("Gateway desconectado (%s). Reintentando en %ds...", e, backoff)

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

        except (websockets.exceptions.WebSocketException, OSError) as e:
            _state["connected"] = False
            _state["last_error"] = str(e)
            _state["reconnect_attempts"] += 1
            log.warning("Error WS (%s). Reintentando en %ds...", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

        except Exception as e:
            _state["connected"] = False
            _state["last_error"] = str(e)
            log.error("Error inesperado en bridge: %s", e, exc_info=True)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)


# ── API pública ──────────────────────────────────────────────────────────────

async def start_bridge() -> None:
    global _bridge_task
    _bridge_task = asyncio.create_task(_run_bridge(), name="openclaw-bridge")
    log.info("Gateway bridge iniciado → %s (agent: %s)", GATEWAY_WS_URL, GATEWAY_AGENT_ID)


async def stop_bridge() -> None:
    global _bridge_task
    if _bridge_task and not _bridge_task.done():
        _bridge_task.cancel()
        try:
            await asyncio.wait_for(_bridge_task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    _state["connected"] = False
    log.info("Gateway bridge detenido.")
