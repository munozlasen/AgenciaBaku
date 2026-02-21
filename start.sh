#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════
#  BAKU_MASTER — Script de inicio completo
#  Levanta: Ollama → verifica modelo → FastAPI (UI + Agente)
# ════════════════════════════════════════════════════════════════

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# Colores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log()  { echo -e "${GREEN}[BAKU]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err()  { echo -e "${RED}[ERR]${NC} $1"; }
step() { echo -e "\n${BOLD}${BLUE}══ $1 ══${NC}"; }

# ── Cargar .env ──────────────────────────────────────────────────
if [ -f "$DIR/.env" ]; then
    export $(grep -v '^#' "$DIR/.env" | grep -v '^$' | xargs)
    log "Variables de entorno cargadas desde .env"
else
    warn ".env no encontrado — usando valores por defecto"
fi

OLLAMA_HOST="${OLLAMA_HOST:-http://127.0.0.1:11434}"
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3:8b}"
OLLAMA_PORT="${OLLAMA_HOST##*:}"
GATEWAY_WS_URL="${GATEWAY_WS_URL:-ws://127.0.0.1:18789}"

# ════════════════════════════════════════════════════════════════
# PASO 1 — Verificar e iniciar Ollama
# ════════════════════════════════════════════════════════════════
step "Verificando Ollama"

if ! command -v ollama &>/dev/null; then
    err "Ollama no está instalado. Instálalo desde: https://ollama.com/download"
    exit 1
fi

# ¿Ya está corriendo?
if curl -sf "${OLLAMA_HOST}/api/tags" &>/dev/null; then
    log "Ollama ya está corriendo en ${OLLAMA_HOST}"
else
    log "Iniciando Ollama en background..."
    ollama serve &>/tmp/baku_ollama.log &
    OLLAMA_PID=$!
    echo "$OLLAMA_PID" > /tmp/baku_ollama.pid

    # Esperar hasta 20s
    for i in {1..20}; do
        sleep 1
        if curl -sf "${OLLAMA_HOST}/api/tags" &>/dev/null; then
            log "Ollama listo (${i}s)"
            break
        fi
        if [ "$i" -eq 20 ]; then
            err "Ollama no respondió en 20s. Revisa: tail -f /tmp/baku_ollama.log"
            exit 1
        fi
    done
fi

# ════════════════════════════════════════════════════════════════
# PASO 2 — Verificar modelo
# ════════════════════════════════════════════════════════════════
step "Verificando modelo: ${OLLAMA_MODEL}"

MODELS_JSON=$(curl -sf "${OLLAMA_HOST}/api/tags" || echo '{"models":[]}')
if echo "$MODELS_JSON" | grep -q "\"${OLLAMA_MODEL}\""; then
    log "Modelo ${OLLAMA_MODEL} disponible"
else
    warn "Modelo ${OLLAMA_MODEL} no encontrado. Descargando..."
    warn "Esto puede tardar varios minutos dependiendo de tu conexión."
    echo ""
    ollama pull "$OLLAMA_MODEL"
    log "Modelo descargado exitosamente"
fi

# ════════════════════════════════════════════════════════════════
# PASO 3 — Gateway WebSocket (corre internamente con FastAPI)
# ════════════════════════════════════════════════════════════════
step "Gateway WebSocket"
log "El Gateway WebSocket se iniciará en ${GATEWAY_WS_URL} (interno)"
GATEWAY_STATUS="INICIANDO CON FASTAPI"

# ════════════════════════════════════════════════════════════════
# PASO 4 — Instalar dependencias Python (si es necesario)
# ════════════════════════════════════════════════════════════════
if ! python3 -c "import fastapi, uvicorn" &>/dev/null; then
    step "Instalando dependencias Python"
    pip install -r "$DIR/requirements.txt" --quiet
    log "Dependencias instaladas"
fi

# ════════════════════════════════════════════════════════════════
# PASO 5 — Iniciar BAKU_MASTER UI + API
# ════════════════════════════════════════════════════════════════
step "Iniciando BAKU_MASTER"

echo ""
echo -e "${BOLD}${GREEN}═══════════════════════════════════════${NC}"
echo -e "${BOLD}  BAKU Agency — Sistema listo${NC}"
echo -e "${BOLD}${GREEN}═══════════════════════════════════════${NC}"
echo ""
echo -e "  ${CYAN}UI / Chat:${NC}     http://localhost:8000"
echo -e "  ${CYAN}API Docs:${NC}      http://localhost:8000/api/docs"
echo -e "  ${CYAN}Modelo:${NC}        ${OLLAMA_MODEL}"
echo -e "  ${CYAN}Gateway:${NC}       ws://localhost:${GATEWAY_WS_URL##*:} (integrado)"
echo ""
echo -e "${YELLOW}  Ctrl+C para detener${NC}"
echo ""

# Cleanup al salir
cleanup() {
    echo ""
    log "Deteniendo servicios..."
    if [ -f /tmp/baku_ollama.pid ]; then
        kill "$(cat /tmp/baku_ollama.pid)" 2>/dev/null || true
        rm -f /tmp/baku_ollama.pid
        log "Ollama detenido"
    fi
    log "BAKU_MASTER detenido"
}
trap cleanup EXIT INT TERM

# Iniciar FastAPI
python3 -m uvicorn api:app \
    --host 0.0.0.0 \
    --port 8000 \
    --log-level info \
    --no-access-log
