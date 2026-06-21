#!/bin/bash
# Setup completo del entorno en Linux/WSL.
# Crea el venv, instala dependencias y descarga Chromium.
#
# Uso (primera vez):
#   bash scripts/setup.sh
#
# Después solo activa el venv:
#   source venv/bin/activate

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "==> [1/5] Creando entorno virtual..."
python3 -m venv venv

echo "==> [2/5] Instalando dependencias Python..."
venv/bin/pip install --upgrade pip -q
venv/bin/pip install -r requirements.txt -q

echo "==> [3/5] Descargando Chromium de Playwright..."
venv/bin/playwright install chromium

echo "==> [4/5] Instalando dependencias de sistema para Chromium..."
# Requiere sudo — omite si ya estan instaladas
if command -v apt-get &>/dev/null; then
    venv/bin/playwright install-deps chromium || \
        echo "  AVISO: install-deps fallo (puede que ya esten instaladas o falte sudo)"
fi

echo "==> [5/5] Creando directorio de datos..."
mkdir -p data/downloads

echo ""
echo "======================================================="
echo " Setup completo. Para activar el entorno:"
echo "   source venv/bin/activate"
echo ""
echo " Prueba rapida (1 puesto, 1 mesa, sin subir):"
echo "   python src/scraper.py --departamento 21 --max-puestos 1 --max-mesas 1"
echo ""
echo " Prueba con Azure:"
echo "   python src/scraper.py --departamento 21 --max-puestos 1 --max-mesas 3 \\"
echo "     --upload --container actas-e14-scraping-1"
echo "======================================================="
