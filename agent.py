"""
OpenClaw MAIN agent connector.

Connects to the OpenClaw gateway (port 18789) using the OpenAI-compatible
chat completions endpoint, with Bearer token auth.

Falls back to direct Ollama if the gateway is unreachable.

Config via .env:
  OPENCLAW_GATEWAY_URL  default: http://127.0.0.1:18789
  OPENCLAW_TOKEN        required for gateway auth
  OPENCLAW_SOUL         path to SOUL.md (default: OpenClaw workspace)
  OLLAMA_URL            fallback: http://127.0.0.1:11434/v1
  OLLAMA_MODEL          fallback model: qwen3:latest
"""

import os
import re
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

# Gateway (OpenClaw)
GATEWAY_URL   = os.getenv("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:18789")
GATEWAY_TOKEN = os.getenv("OPENCLAW_TOKEN", "")

# Fallback (direct Ollama)
OLLAMA_URL    = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/v1")
OLLAMA_MODEL  = os.getenv("OLLAMA_MODEL", "qwen3:latest")

# SOUL.md
SOUL_PATH = Path(os.getenv(
    "OPENCLAW_SOUL",
    r"C:\OpenClawWorkspace\.openclaw\workspace\SOUL.md"
))

_FALLBACK_SOUL = (
    "You are BAKU_MASTER, the central intelligence of Baku Agency. "
    "Operate with clarity, decisiveness, and precision."
)

LEADS_CONTEXT = """

## LEADS ENGINE PLATFORM
You have a connected Leads Engine at http://localhost:8000.
The sidebar shows real-time stats and the leads list.
When the user asks about leads, guide them to use the sidebar panel or the following API:
  POST /leads              - register a new lead
  GET  /leads              - list leads (filter: ?status=new|contacted|qualified|converted|discarded)
  PATCH /leads/{id}/status - update lead status
  GET  /stats              - lead counts by status and source
Always identify your active MODE before responding.
"""


def load_soul() -> str:
    text = SOUL_PATH.read_text(encoding="utf-8") if SOUL_PATH.exists() else _FALLBACK_SOUL
    return text + LEADS_CONTEXT


def strip_think(text: str) -> tuple[str, str]:
    """Separate <think>...</think> from the visible response."""
    match = re.search(r"<think>(.*?)</think>", text, re.DOTALL)
    thinking = match.group(1).strip() if match else ""
    clean = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    return clean, thinking


def detect_mode(text: str) -> str:
    modes = [
        "CEO_MODE", "CFO_MODE", "META_ADS_MODE", "GOOGLE_ADS_MODE",
        "INFLUENCER_MODE", "CONTENT_ENGINE_MODE", "LEADS_ENGINE_MODE",
        "OPTIMIZER_MODE", "PERFORMANCE_MONITOR", "CFO_AUDIT",
        "CONTENT_AUTOMATION",
    ]
    for mode in modes:
        if mode in text:
            return mode
    return "BAKU_MASTER"


async def _call_gateway(messages: list[dict]) -> str:
    """Call OpenClaw gateway (OpenAI-compatible, with Bearer token)."""
    headers = {"Authorization": f"Bearer {GATEWAY_TOKEN}"}
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(
            f"{GATEWAY_URL}/v1/chat/completions",
            headers=headers,
            json={
                "model": "ollama-local/qwen3:latest",
                "messages": messages,
                "stream": False,
            },
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


async def _call_ollama(messages: list[dict]) -> str:
    """Fallback: call Ollama directly."""
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(
            f"{OLLAMA_URL}/chat/completions",
            json={"model": OLLAMA_MODEL, "messages": messages, "stream": False},
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


async def chat(messages: list[dict]) -> dict:
    """
    Send messages to BAKU_MASTER.
    Tries OpenClaw gateway first, falls back to direct Ollama.
    Returns: {content, thinking, mode, error, via}
    """
    system = load_soul()
    full = [{"role": "system", "content": system}] + messages

    raw = None
    via = "unknown"

    # Try gateway
    if GATEWAY_TOKEN:
        try:
            raw = await _call_gateway(full)
            via = "openclaw-gateway"
        except httpx.ConnectError:
            pass  # gateway not running, fall through
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                return {"content": "", "thinking": "", "mode": "", "via": "",
                        "error": "Token invalido para el gateway de OpenClaw. Revisa OPENCLAW_TOKEN en .env"}
            pass  # other error, fall through

    # Fallback to Ollama
    if raw is None:
        try:
            raw = await _call_ollama(full)
            via = "ollama-direct"
        except httpx.ConnectError:
            return {
                "content": "", "thinking": "", "mode": "", "via": "",
                "error": "No se puede conectar a OpenClaw ni a Ollama. Verifica que esten corriendo.",
            }
        except Exception as e:
            return {"content": "", "thinking": "", "mode": "", "via": "", "error": str(e)}

    content, thinking = strip_think(raw)
    return {
        "content": content,
        "thinking": thinking,
        "mode": detect_mode(content),
        "via": via,
        "error": None,
    }
