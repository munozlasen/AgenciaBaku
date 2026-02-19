"""
OpenClaw MAIN agent connector.

Connects directly to Ollama using the native API.
Loads SOUL.md from the OpenClaw workspace as system prompt.

Config via .env:
  OLLAMA_HOST   default: http://127.0.0.1:11434
  OLLAMA_MODEL  default: qwen3:4b
  OPENCLAW_SOUL path to SOUL.md
"""

import os
import re
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

OLLAMA_HOST  = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:1.7b")
SOUL_PATH    = Path(os.getenv(
    "OPENCLAW_SOUL",
    r"C:\OpenClawWorkspace\.openclaw\workspace\SOUL.md"
))

_FALLBACK_SOUL = (
    "You are BAKU_MASTER, the central intelligence of Baku Agency. "
    "Operate with clarity, decisiveness, and precision."
)

_LEADS_CONTEXT = """

## LEADS ENGINE
You have a connected Leads Engine at http://localhost:8000.
The sidebar shows real-time stats and the leads list.
Endpoints available:
  POST /leads              - register a new lead
  GET  /leads              - list leads (?status=new|contacted|qualified|converted|discarded)
  PATCH /leads/{id}/status - update lead status
  GET  /stats              - counts by status and source
Always identify your active MODE before responding.
"""


def _load_soul() -> str:
    text = SOUL_PATH.read_text(encoding="utf-8") if SOUL_PATH.exists() else _FALLBACK_SOUL
    return text + _LEADS_CONTEXT


def _strip_think(text: str) -> tuple[str, str]:
    match = re.search(r"<think>(.*?)</think>", text, re.DOTALL)
    thinking = match.group(1).strip() if match else ""
    clean = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    if not clean and thinking:
        clean = thinking
        thinking = ""
    return clean, thinking


def _detect_mode(text: str) -> str:
    for mode in [
        "CEO_MODE", "CFO_MODE", "META_ADS_MODE", "GOOGLE_ADS_MODE",
        "INFLUENCER_MODE", "CONTENT_ENGINE_MODE", "LEADS_ENGINE_MODE",
        "OPTIMIZER_MODE", "PERFORMANCE_MONITOR", "CFO_AUDIT", "CONTENT_AUTOMATION",
    ]:
        if mode in text:
            return mode
    return "BAKU_MASTER"


def _inject_no_think(messages: list[dict]) -> list[dict]:
    """Prepend /no_think to the last user message — qwen3 native directive."""
    msgs = [m.copy() for m in messages]
    for m in reversed(msgs):
        if m["role"] == "user":
            if not m["content"].startswith("/no_think"):
                m["content"] = "/no_think " + m["content"]
            break
    return msgs


async def chat(messages: list[dict]) -> dict:
    """Send messages to BAKU_MASTER via Ollama native API."""
    full_messages = [{"role": "system", "content": _load_soul()}] + _inject_no_think(messages)

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
            content = data["message"].get("content", "").strip()
            thinking = data["message"].get("thinking", "")
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
            "content": "", "thinking": "", "mode": "", "via": "",
            "error": "No se puede conectar a Ollama. Verifica que este corriendo en http://127.0.0.1:11434",
        }
    except Exception as e:
        return {"content": "", "thinking": "", "mode": "", "via": "", "error": str(e)}
