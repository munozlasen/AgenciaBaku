"""
OpenClaw MAIN agent connector.

Reads SOUL.md from the OpenClaw workspace and forwards chat messages
to the local Ollama instance using the OpenAI-compatible API.
"""

import os
import re
from pathlib import Path

import httpx

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/v1")
MODEL = os.getenv("OLLAMA_MODEL", "qwen3:latest")
SOUL_PATH = Path(os.getenv(
    "OPENCLAW_SOUL",
    r"C:\OpenClawWorkspace\.openclaw\workspace\SOUL.md"
))

_FALLBACK_SOUL = (
    "You are BAKU_MASTER, the central intelligence of Baku Agency. "
    "You coordinate all strategic, financial, marketing, and operational decisions. "
    "Operate with clarity, decisiveness, and precision."
)

LEADS_CONTEXT = """
You also have access to a Leads Engine platform. When the user asks about leads,
you can guide them to use the UI panel on the left, or they can call:
  POST /leads       — register a new lead
  GET  /leads       — list all leads
  GET  /stats       — view lead statistics
  PATCH /leads/{id}/status — update lead status

Always identify which internal MODE you are operating in before responding.
"""


def load_soul() -> str:
    if SOUL_PATH.exists():
        return SOUL_PATH.read_text(encoding="utf-8") + "\n\n" + LEADS_CONTEXT
    return _FALLBACK_SOUL + "\n\n" + LEADS_CONTEXT


def strip_think(text: str) -> tuple[str, str]:
    """Separate <think>...</think> blocks from the main response."""
    think_match = re.search(r"<think>(.*?)</think>", text, re.DOTALL)
    thinking = think_match.group(1).strip() if think_match else ""
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


async def chat(messages: list[dict]) -> dict:
    """
    Send messages to Ollama and return the assistant reply.
    messages: list of {role, content} — WITHOUT system prompt (added here).
    Returns: {content, thinking, mode, error}
    """
    system_prompt = load_soul()
    full_messages = [{"role": "system", "content": system_prompt}] + messages

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                f"{OLLAMA_URL}/chat/completions",
                json={"model": MODEL, "messages": full_messages, "stream": False},
            )
            r.raise_for_status()
            raw = r.json()["choices"][0]["message"]["content"]
            content, thinking = strip_think(raw)
            return {
                "content": content,
                "thinking": thinking,
                "mode": detect_mode(content),
                "error": None,
            }
    except httpx.ConnectError:
        return {
            "content": "",
            "thinking": "",
            "mode": "",
            "error": "No se puede conectar a Ollama. Verifica que este corriendo en http://127.0.0.1:11434",
        }
    except Exception as e:
        return {
            "content": "",
            "thinking": "",
            "mode": "",
            "error": f"Error: {str(e)}",
        }
