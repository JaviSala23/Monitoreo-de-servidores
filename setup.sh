#!/bin/bash
# Script de instalación de dependencias para Server Monitor
set -e
echo "=== Server Monitor — Instalación ==="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Crear entorno virtual si no existe
if [ ! -d ".venv" ]; then
    echo "Creando entorno virtual..."
    python3 -m venv .venv
fi

# Activar e instalar
source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q

echo ""
echo "✅ Instalación completa."
echo "▶  Para ejecutar:  source .venv/bin/activate && python main.py"
