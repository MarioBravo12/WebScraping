# Scraper E-14 — Registraduria Nacional
#
# Base: python:3.11-slim (Debian Bookworm) + Xvfb para navegador headed.
# Akamai bloquea el modo headless, por eso NECESITAMOS Xvfb.
#
# Build:
#   docker build -t e14-scraper .
#
# Run (ejemplo VM1):
#   docker run --rm --env-file .env \
#     -v $(pwd)/data:/app/data \
#     e14-scraper src/loop_runner.py \
#       --departamento 01 --workers 8 --machine-id vm1 \
#       --municipios 001,007,010,...

FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    # xvfb-run asigna el DISPLAY automaticamente con -a, pero lo dejamos
    # como fallback por si algun script lo necesita directamente.
    DISPLAY=:99

# Dependencias de sistema: Xvfb + libs requeridas por Chromium (playwright install-deps)
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    # Chromium deps (subset; playwright install-deps agrega el resto)
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcairo2 \
    libcups2 \
    libdbus-1-3 \
    libexpat1 \
    libfontconfig1 \
    libgbm1 \
    libglib2.0-0 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxcursor1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxi6 \
    libxrandr2 \
    libxrender1 \
    libxss1 \
    libxtst6 \
    lsb-release \
    wget \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instala dependencias Python primero (capa cacheable)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Instala Chromium de Playwright (incluye sus propias libs compiladas)
RUN playwright install chromium
# Instala las dependencias de sistema que Playwright necesita y no estan arriba
RUN playwright install-deps chromium

# Copia el codigo fuente
COPY . .

# Crea directorios de datos (el volume los sobreescribira en runtime)
RUN mkdir -p data/downloads

# Punto de entrada: xvfb-run lanza Xvfb automaticamente antes de Python
# -a = auto-selecciona numero de display disponible
# --server-args = resolucion virtual 1920x1080 color 24-bit
ENTRYPOINT ["xvfb-run", "-a", "--server-args=-screen 0 1920x1080x24", "python"]
CMD ["src/loop_runner.py", "--help"]
