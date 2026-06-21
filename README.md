# Scraper E-14 — Registraduría Nacional

Descarga automatizada de los formularios E-14 publicados en el portal
"Visor Ciudadano" de la Registraduría Nacional
(`divulgacione14presidente.registraduria.gov.co`), y los sube a Azure Blob
Storage. Ver diagrama del pipeline en [docs/pipeline_diagram.png](docs/pipeline_diagram.png).

## Por qué no es un scraper HTTP simple

El portal es una SPA en Angular con protección Akamai. Esto tiene tres
consecuencias de diseño importantes:

1. **No hay API REST simple**: hay que automatizar la navegación real con
   Playwright (clic en Departamento → Municipio → Zona → Puesto → Consultar →
   ícono "Descargar"), igual que lo haría una persona.
2. **El modo headless está bloqueado**: Akamai lo detecta a nivel de huella
   TLS/HTTP2 (no es un tema de flags de Chromium — se confirmó con Playwright
   actualizado a la última versión). El navegador debe correr en modo
   **headed** (con interfaz, aunque sea virtual vía Xvfb en un servidor).
3. **El PDF real se obtiene interceptando la respuesta de red** de
   `/assets/temis/pdf/{depto}/{municipio}/{zona}/{puesto}/{mesa}/{corp}/{hash}.pdf`,
   no leyendo el visor (que lo muestra como blob en memoria).

## Setup local

**Linux / WSL (un solo comando):**
```bash
bash scripts/setup.sh
source venv/bin/activate
```

**Manual (o Windows):**
```bash
python -m venv venv
source venv/bin/activate           # Linux/WSL
# venv\Scripts\activate            # Windows
pip install -r requirements.txt
playwright install chromium        # descarga el binario del browser
playwright install-deps chromium   # solo Linux — instala libs de sistema
mkdir -p data/downloads
cp .env.example .env               # edita .env con tu connection string real
```

> `playwright install chromium` **no va en requirements.txt** porque no es un paquete pip — descarga el binario del browser. Es obligatorio correrlo una vez por entorno (venv) en cada máquina.

`reference_data/` ya incluye los árboles de Departamento→Municipio→Zona→Puesto
y los códigos de departamento/corporación para la elección Presidente 2026.
Si la Registraduría habilita una elección nueva, regenéralos con:

```bash
python src/fetch_tree_json.py
```

## Uso

### Un solo navegador (modo simple/depuración)

```bash
python src/scraper.py --departamento 01 --max-puestos 99999 --max-mesas 99999 \
  --upload --container actas-e14-scraping-1
```

- `--departamento`: código DIVIPOLA (01=Antioquia, 03=Atlántico, 21=Magdalena — ver `reference_data/allDepartments.json`)
- `--container`: contenedor Azure destino (**requerido con `--upload`**). Valores: `actas-e14-scraping-1` | `actas-e14-scraping-2`
- `--upload`: sube cada PDF (y su metadata) a Azure al terminar de descargarlo. Si se omite, solo queda local en `data/downloads/`
- `--excel`: ruta del Excel de registro (default: `data/registro_actas.xlsx`). Evita re-subir actas ya enviadas en ejecuciones anteriores
- `--municipio --zona --puesto`: apunta a un puesto específico (útil para pruebas)
- `--no-resume`: ignora el checkpoint y vuelve a procesar todo

### Estructura de archivos en Azure Blob Storage

Cada mesa sube exactamente dos archivos a su propia carpeta:

```
{contenedor}/
  {depto}/{municipio}/{lugar}/{zona}/{puesto}/{mesa}/
    acta.pdf
    metadata.json
```

Ejemplo real:
```
actas-e14-scraping-1/
  21/008/colegio_san_jose_de_la_montana/99/10/001/
    acta.pdf
    metadata.json
```

`lugar` = nombre del puesto de votación normalizado (minúsculas, sin tildes, espacios → `_`).

### Metadata de cada mesa

El `metadata.json` contiene:

```json
{
  "departamento_codigo": "21", "departamento_nombre": "MAGDALENA",
  "municipio_codigo": "008", "municipio_nombre": "ALGARROBO",
  "zona_codigo": "99", "puesto_codigo": "10", "puesto_nombre": "BELLAVISTA",
  "mesa_numero": "001",
  "corporacion_acronimo": "PRE", "corporacion_nombre": "PRESIDENTE",
  "url_descarga": "https://...",
  "sha256": "abc123...",
  "tamano_bytes": 100340,
  "numero_paginas": 2,
  "mime_tipo": "application/pdf",
  "hash_archivo_original": "769b9443...eb596.pdf",
  "fecha_descarga_utc": "2026-06-21T17:23:10+00:00",
  "container": "actas-e14-scraping-1",
  "blob_nombre": "21/008/bellavista/99/10/001/acta.pdf",
  "blob_url": "https://datae14.blob.core.windows.net/...",
  "blob_metadata_url": "https://datae14.blob.core.windows.net/...",
  "fecha_subida_utc": "2026-06-21T17:23:15+00:00"
}
```

### Paralelo (recomendado para correr departamentos completos)

```bash
python src/scraper_parallel.py --departamento 01 --workers 8 \
  --upload --container actas-e14-scraping-1
```

Reparte los puestos del departamento entre N procesos, cada uno con su propio
navegador y su propio checkpoint (`data/checkpoint_{depto}_w{N}.json`, o
`data/checkpoint_{depto}_{machine-id}_w{N}.json` si se usa `--machine-id`).
Al terminar todos los workers, fusiona sus registros Excel parciales en
`data/registro_actas.xlsx` para que la próxima ejecución salte lo ya subido.

Comandos para los 3 departamentos del piloto:

```bash
python src/scraper_parallel.py --departamento 01 --workers 8 \
  --upload --container actas-e14-scraping-1   # Antioquia

python src/scraper_parallel.py --departamento 03 --workers 8 \
  --upload --container actas-e14-scraping-1   # Atlántico

python src/scraper_parallel.py --departamento 21 --workers 8 \
  --upload --container actas-e14-scraping-1   # Magdalena
```

### Loop iterativo 24h (modo producción)

El runner repite el scraper cada 10 minutos durante 24 horas. Entre iteraciones,
el Excel de registro y los checkpoints garantizan que no se re-descarguen ni
re-suban actas ya enviadas. El contenedor se selecciona automáticamente según
la hora Colombia:

- **00:00 – 22:59** → `actas-e14-scraping-1`
- **23:00 – 23:59** → `actas-e14-scraping-2`

```bash
# Un departamento completo
python src/loop_runner.py --departamento 21 --workers 8 --machine-id vm4

# Con municipios específicos (para repartir entre VMs)
python src/loop_runner.py --departamento 01 --workers 8 --machine-id vm1 \
  --municipios 001,007,010,...
```

## Rendimiento medido (1 máquina física, 16 vCPU)

| Workers | Throughput medido | Antioquia (15,801 mesas) |
|---|---|---|
| 1 | 1.16 mesas/seg | ~4.3 h |
| 4 | 2.46 mesas/seg | ~1.8 h |
| 8 | 3.23 mesas/seg | ~1.4 h |

Más de 8 workers en una sola máquina tiene retornos decrecientes (contención
de CPU entre instancias de Chromium visibles — confirmado, no es límite del
sitio). Para bajar de 1 hora hay que repartir los workers entre **varias
máquinas virtuales**, no apilar más en una sola.

Atlántico (6,190 mesas) y Magdalena (3,228 mesas) sí caben dentro de 1 hora
con 8 workers en una sola máquina (~30 min y ~17 min respectivamente).

## Plan de despliegue actual: 4 máquinas

Antioquia se reparte en **2 VMs** (es el único departamento que no cabe en 1h
con una sola máquina); Atlántico y Magdalena usan **1 VM cada uno** (ya caben
en menos de 1h solos). En cada VM corre el mismo repo, mismo `.env`, mismo
`reference_data/` — lo único que cambia es el comando.

> Listado completo de municipios por departamento (códigos y cantidad de
> mesas, para verificar que ningún municipio quede repetido ni fuera de los
> grupos): [docs/municipios_por_departamento.md](docs/municipios_por_departamento.md)

### Docker (recomendado — incluye Xvfb automático)

```bash
# Build (una sola vez o cuando cambia el código)
docker compose build

# Arrancar la VM correspondiente
docker compose --profile vm1 up -d   # VM 1
docker compose --profile vm2 up -d   # VM 2
docker compose --profile vm3 up -d   # VM 3
docker compose --profile vm4 up -d   # VM 4

# Ver logs en tiempo real
docker compose --profile vm1 logs -f

# Detener
docker compose --profile vm1 down
```

### Sin Docker (manual con Xvfb)

Cada VM ejecuta `loop_runner.py` dentro de `xvfb-run`:

#### VM 1 — Antioquia, mitad 1 (56 municipios, 7,904 mesas, ~41 min/iter)

```bash
xvfb-run -a python src/loop_runner.py \
  --departamento 01 --workers 8 --machine-id vm1 \
  --municipios 001,007,010,016,025,031,034,037,046,052,055,058,064,076,079,082,085,091,109,117,127,130,139,142,168,170,181,184,187,190,191,193,196,199,202,205,206,211,217,220,226,227,232,237,244,250,253,265,270,274,282,289,291,292,295,300
```

#### VM 2 — Antioquia, mitad 2 (69 municipios, 7,897 mesas, ~41 min/iter)

```bash
xvfb-run -a python src/loop_runner.py \
  --departamento 01 --workers 8 --machine-id vm2 \
  --municipios 004,013,019,022,028,035,039,040,043,049,061,062,067,070,073,078,080,088,094,097,100,103,106,112,115,118,121,124,133,136,140,145,148,150,151,154,157,160,163,166,169,172,175,178,192,208,214,218,223,229,230,231,235,238,241,247,256,259,262,268,271,277,280,283,286,290,293,298,301
```

#### VM 3 — Atlántico, departamento completo (6,190 mesas, ~30 min/iter)

```bash
xvfb-run -a python src/loop_runner.py \
  --departamento 03 --workers 8 --machine-id vm3
```

#### VM 4 — Magdalena, departamento completo (3,228 mesas, ~17 min/iter)

```bash
xvfb-run -a python src/loop_runner.py \
  --departamento 21 --workers 8 --machine-id vm4
```

`--machine-id` evita colisiones de checkpoint si las VMs llegaran a compartir
almacenamiento (cada una escribe `data/checkpoint_01_vm1_w{N}.json`, etc.).
Si una VM se interrumpe, vuelve a correr exactamente el mismo comando —
retoma donde quedó gracias al checkpoint y al registro Excel.

### Cómo modificar el reparto (otro número de VMs, otros departamentos)

1. **Cambiar de departamento**: cambia `--departamento` (códigos en
   `reference_data/allDepartments.json` o en
   [docs/municipios_por_departamento.md](docs/municipios_por_departamento.md)).
2. **Repartir un departamento entre N máquinas distinto de 2**: en vez de
   copiar los códigos a mano, genera el reparto balanceado por mesas (no por
   cantidad de municipios — varían de 2 a más de 5,000 mesas cada uno):

   ```bash
   python src/plan_split.py --departamento 01 --maquinas 3 --workers-por-maquina 8 --upload
   ```

   Esto imprime el comando exacto y balanceado para cada VM — cópialo
   directo al README o al script de arranque de cada máquina.
3. **Apuntar una VM a municipios específicos a mano** (sin `plan_split.py`):
   usa `--municipios codigo1,codigo2,...` directamente.
4. **Regenerar el listado de municipios** (por ejemplo si se agrega un
   departamento nuevo al plan): `python src/list_municipios.py --departamentos 01,03,21,XX`

## Registro de actas enviadas (Excel)

Cada vez que se sube un PDF, el scraper registra la mesa en
`data/registro_actas.xlsx`. En la próxima ejecución (del loop o manual), el
scraper carga ese registro y **omite automáticamente las mesas ya subidas** —
ni las descarga ni las vuelve a subir.

En modo paralelo, cada worker escribe en su propio archivo temporal
(`registro_actas_{machine-id}_w{N}.xlsx`) y al terminar todos se fusionan en
el maestro. Así no hay conflictos de escritura entre procesos.

## Qué hacer si cambia la URL del portal

El link `https://e14segundavueltapresidente.registraduria.gov.co/home` es
específico de esta elección (probablemente cambie para la segunda vuelta o
una elección futura). **No hay que tocar el código** para actualizarlo:

1. Abre (o crea) el archivo `.env` en cada máquina.
2. Agrega/edita la línea: `PORTAL_URL=https://nueva-url-aqui/home`
3. Vuelve a correr el comando normalmente.

**Importante**: esto solo cubre un cambio de *dominio o ruta inicial*. Si la
Registraduría cambia la estructura de navegación (nuevos componentes Angular,
otro flujo de selección, etc.), hay que volver a inspeccionar el DOM y
actualizar los selectores en `src/scraper.py`.

## Especificaciones de la máquina de despliegue

### Sistema operativo

**Linux (Ubuntu 22.04 LTS o superior) con Xvfb**, no Windows Server.

Razón: el navegador debe correr en modo headed (ver arriba), pero un servidor
no tiene pantalla física. Xvfb provee una pantalla virtual sin necesidad de
monitor ni sesión RDP activa.

```bash
sudo apt update && sudo apt install -y xvfb
```

### Hardware

| Recurso | Mínimo | Recomendado |
|---|---|---|
| vCPU | 8 | 8 (no se justifica mas en una sola maquina, ver tabla de arriba) |
| RAM | 8 GB | 16 GB |
| Disco | 10 GB libres | 20 GB libres |

- RAM: cada instancia de Chromium headed consume aprox. 0.5–1 GB; con 8
  workers + sistema operativo, 16 GB da margen cómodo.
- Disco: ~25,200 mesas totales (3 departamentos) × ~95 KB ≈ 2.5 GB de PDFs,
  más binarios de Chromium (~500 MB) y dependencias de Python.

### Software

- Python 3.11+
- Dependencias de `requirements.txt`: `pip install -r requirements.txt`
- Chromium de Playwright: `playwright install chromium`
- Dependencias de sistema de Chromium en Linux: `playwright install-deps chromium`

### Red

Acceso saliente HTTPS (443) sin restricciones a:

- `divulgacione14presidente.registraduria.gov.co` (portal y assets estáticos)
- `apx2e14awsprodpresidencia.prdtpssas.com` (WebSocket `wss://`, suscripción GraphQL en tiempo real)
- `cognito-identity.us-east-2.amazonaws.com` (credenciales temporales AWS Amplify)
- `www.google.com`, `www.gstatic.com` (reCAPTCHA invisible — debe poder cargar, aunque no se resuelva activamente)
- `*.blob.core.windows.net` (subida a Azure Storage)

Un proxy corporativo que bloquee WebSockets o dominios de Google/reCAPTCHA
romperá el flujo.

### Secretos

`.env` (nunca commitear) con:

```
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=...
AZURE_STORAGE_CONTAINER=actas-e14-scraping-1
```

En el servidor de despliegue, preferir inyectar esto como variables de
entorno del proceso/contenedor en lugar de un archivo `.env` en disco.

### Operación

- Cada VM tarda entre ~17 min (Magdalena) y ~41 min (cada mitad de
  Antioquia) por iteración del loop.
- El proceso debe sobrevivir el cierre de la sesión SSH: usar Docker
  (`docker compose --profile vmN up -d`), `tmux`, `screen` o `nohup`.
- Los checkpoints (`data/checkpoint_{depto}_{machine-id}_w{N}.json`) permiten
  reanudar sin perder progreso — simplemente correr el mismo comando de nuevo.
- El Excel de registro (`data/registro_actas.xlsx`) evita re-subir actas ya
  enviadas en iteraciones previas del loop.
- Logs por worker en `data/worker_{depto}_{machine-id}_{N}.log`.

## Riesgos conocidos

- **reCAPTCHA invisible**: no se disparó en las pruebas realizadas (hasta 8
  workers concurrentes, cientos de mesas), pero no hay garantía de que no
  aparezca a mayor volumen sostenido. Si aparece, el scraper se detendrá en
  ese puesto (excepción capturada, pasa al siguiente) pero no puede resolver
  el desafío automáticamente.
- **Cambios en el DOM del portal**: los selectores (`app-custom-select`,
  `.open-pdf`, etc.) son específicos de esta versión del sitio. Un rediseño
  del portal requeriría actualizar `src/scraper.py`.
- **Dependencia de `reference_data/departmentsTree.json`**: si la Registraduría
  cambia los códigos de puesto/zona para una elección futura, hay que
  regenerar este archivo con `src/fetch_tree_json.py` antes de correr el
  scraper.
