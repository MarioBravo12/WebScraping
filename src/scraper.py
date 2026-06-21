"""
Scraper de formularios E-14 - Registraduria Nacional (portal Visor Ciudadano).

Recorre Departamento -> Municipio -> Zona -> Puesto -> Mesa automatizando la
navegacion real del sitio (los selects son componentes Angular personalizados,
no <select> nativos), intercepta la respuesta HTTP que entrega el PDF real
(antes de que el navegador lo convierta en blob) y sube cada archivo a Azure
Blob Storage.

Uso:
    python src/scraper.py --departamento 01 --max-puestos 1 --max-mesas 2

Por seguridad, por defecto SOLO procesa 1 puesto y 2 mesas (modo de prueba).
Aumenta --max-puestos/--max-mesas explicitamente para correr a mayor escala.
"""
import argparse
import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from azure_uploader import upload_bytes

load_dotenv()

# Si la Registraduria cambia el dominio/ruta del portal (ej. para un dia de
# elecciones distinto o una segunda vuelta), defina PORTAL_URL en .env -- NO
# hace falta tocar el codigo. La estructura de navegacion (Departamento ->
# Municipio -> Zona -> Puesto -> Mesa) deberia seguir siendo la misma SPA.
PORTAL_URL = os.environ.get("PORTAL_URL") or "https://divulgacione14presidente.registraduria.gov.co/home"
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
REFERENCE_DIR = os.path.join(os.path.dirname(__file__), "..", "reference_data")
TREE_PATH = os.path.join(REFERENCE_DIR, "departmentsTree.json")
DOWNLOADS_DIR = os.path.join(DATA_DIR, "downloads")


def _load_corporations():
    path = os.path.join(REFERENCE_DIR, "allCorporations.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            edge["node"]["acronym"]: edge["node"]["nameCorporation"]
            for edge in data["data"]["allCorporations"]["edges"]
        }
    except (FileNotFoundError, KeyError):
        return {}


CORPORATIONS = _load_corporations()


def load_tree(departamento_code: str):
    with open(TREE_PATH, "r", encoding="utf-8") as f:
        tree = json.load(f)
    for edge in tree["data"]["departmentsTree"]["edges"]:
        node = edge["node"]
        if node["idDepartmentCode"] == departamento_code:
            return node
    raise ValueError(f"Departamento {departamento_code} no encontrado en departmentsTree.json")


def open_dropdown(page, select_locator, wait_ms=250):
    inp = select_locator.query_selector("input.custom-input")
    inp.click()
    page.wait_for_timeout(wait_ms)
    dropdown = select_locator.query_selector(".dropdown-list")
    if not dropdown:
        return []
    return dropdown.query_selector_all("li")


def click_item_by_code(page, select_locator, code: str, wait_ms=250, mode="prefix"):
    """Abre el dropdown y hace clic en el item que coincide con `code`.
    mode="prefix": el texto empieza con el codigo (Municipio, Puesto: "004 - ABEJORRAL").
    mode="zona": el texto es tipo "Zona 00"."""
    items = open_dropdown(page, select_locator, wait_ms)
    for item in items:
        p = item.query_selector("p")
        text = (p.inner_text() if p else "").strip()
        if mode == "zona":
            matches = text.lower() == f"zona {code}".lower()
        else:
            matches = text.startswith(code)
        if matches:
            p.click()
            page.wait_for_timeout(wait_ms)
            return True
    return False


def dismiss_blocking_modal(page, log=None, wait_ms=300):
    """Busca cualquier boton visible con texto 'Aceptar' (modal de aviso distinto
    al visor de PDF) y le hace clic. Sin esto, el sitio se queda bloqueado y no
    deja continuar con las siguientes mesas."""
    clicked_any = False
    buttons = page.query_selector_all("button")
    for btn in buttons:
        try:
            if not btn.is_visible():
                continue
            text = btn.inner_text().strip().lower()
            if "aceptar" in text:
                btn.click()
                page.wait_for_timeout(wait_ms)
                clicked_any = True
                if log:
                    log("  (cerrado modal de aviso 'Aceptar')")
        except Exception:
            continue
    return clicked_any


def download_mesas_for_puesto(page, depto_code, depto_name, municipio_code, municipio_name,
                               zona_code, puesto_code, puesto_name, max_mesas, log,
                               checkpoint_path, done_mesas, upload):
    """Asume que ya se navego al departamento y se hizo clic en Consultar.
    Itera las tarjetas 'Mesa N' visibles (con paginacion) y descarga cada PDF,
    junto con un .json de metadata (codigos, nombres, hash original, fecha).

    Cada mesa se sube (si upload=True) y se marca en el checkpoint INMEDIATAMENTE
    al terminar, no al final del puesto -- asi una corrida interrumpida a medio
    puesto no pierde el progreso de las mesas que ya alcanzo a completar.

    Las mesas que ya estan en `done_mesas` (de una corrida anterior) se saltan
    sin volver a descargarlas. Esto es clave porque en una eleccion en curso
    los resultados se publican progresivamente: un puesto puede tener solo
    algunas mesas disponibles hoy y el resto mañana -- correr el scraper de
    nuevo debe completar solo lo que falta, no repetir ni perder lo ya hecho."""
    puesto_prefix = f"{municipio_code}|{zona_code}|{puesto_code}|"
    already_done = {k for k in done_mesas if k.startswith(puesto_prefix)}
    newly_downloaded = []
    seen_mesas = set()

    page_num = 1
    while len(already_done) + len(newly_downloaded) < max_mesas:
        cards = page.query_selector_all("app-consult .item-table.isAvailable")
        if not cards:
            break

        progressed = False
        for card in cards:
            if len(already_done) + len(newly_downloaded) >= max_mesas:
                break
            title_el = card.query_selector("h3")
            mesa_label = title_el.inner_text().strip() if title_el else ""
            if mesa_label in seen_mesas:
                continue
            seen_mesas.add(mesa_label)
            progressed = True

            mesa_num = re.sub(r"\D", "", mesa_label) or "0"
            mesa_key = f"{puesto_prefix}{mesa_num}"
            if mesa_key in already_done:
                continue  # ya se descargo (y subio) en una corrida anterior

            # Usamos el icono de "Descargar" (no "Ver"): dispara la misma peticion
            # del PDF real pero sin abrir el visor pesado de pdf.js, y solo deja
            # un modal liviano "Descarga Exitosa" con boton Aceptar.
            download_icon = card.query_selector(".open-pdf")
            try:
                with page.expect_response(
                    lambda r: "/assets/temis/pdf/" in r.url and r.status == 200, timeout=8000
                ) as resp_info:
                    download_icon.click()
                response = resp_info.value
                pdf_bytes = response.body()

                # La URL real tiene el patron:
                # .../assets/temis/pdf/{depto}/{municipio}/{zona}/{puesto}/{mesa}/{corp}/{hash}.pdf
                # de ahi sacamos el hash original y el codigo de corporacion
                # sin tener que adivinarlos.
                url_path = urlsplit(response.url).path
                path_parts = url_path.rstrip("/").split("/")
                corp_code = path_parts[-2] if len(path_parts) >= 2 else None
                hash_filename = path_parts[-1] if path_parts else None

                fname = f"{depto_code}_{municipio_code}_{zona_code}_{puesto_code}_{mesa_num.zfill(3)}.pdf"
                json_fname = os.path.splitext(fname)[0] + ".json"

                metadata = {
                    "departamento_codigo": depto_code,
                    "departamento_nombre": depto_name,
                    "municipio_codigo": municipio_code,
                    "municipio_nombre": municipio_name,
                    "zona_codigo": zona_code,
                    "puesto_codigo": puesto_code,
                    "puesto_nombre": puesto_name,
                    "mesa_numero": mesa_num,
                    "corporacion_acronimo": corp_code,
                    "corporacion_nombre": CORPORATIONS.get(corp_code),
                    "hash_archivo_original": hash_filename,
                    "tamano_bytes": len(pdf_bytes),
                    "fecha_descarga_utc": datetime.now(timezone.utc).isoformat(),
                }
                metadata_bytes = json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8")

                local_path = None
                metadata_path = None
                blob_url = None
                metadata_blob_url = None

                if upload:
                    # Se sube directo desde memoria, sin tocar el disco local.
                    prefix = f"e14/{depto_code}/{municipio_code}/{zona_code}/{puesto_code}"
                    blob_url = upload_bytes(pdf_bytes, f"{prefix}/{fname}")
                    metadata_blob_url = upload_bytes(metadata_bytes, f"{prefix}/{json_fname}")
                else:
                    # Sin --upload no hay donde mas dejarlo: se guarda local para poder inspeccionarlo.
                    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
                    local_path = os.path.join(DOWNLOADS_DIR, fname)
                    with open(local_path, "wb") as f:
                        f.write(pdf_bytes)
                    metadata_path = os.path.join(DOWNLOADS_DIR, json_fname)
                    with open(metadata_path, "wb") as f:
                        f.write(metadata_bytes)

                log(f"  Descargado {mesa_label} -> {fname} ({len(pdf_bytes)} bytes)"
                    + (f" -> {blob_url}" if blob_url else ""))
                newly_downloaded.append({
                    "mesa": mesa_label, "local_path": local_path, "metadata_path": metadata_path,
                    "blob_url": blob_url, "metadata_blob_url": metadata_blob_url,
                })
                append_checkpoint(checkpoint_path, done_mesas, mesa_key)
                already_done.add(mesa_key)
            except Exception as e:
                log(f"  AVISO: no se pudo interceptar el PDF de {mesa_label}: {e!r}")

            dismiss_blocking_modal(page, log, wait_ms=150)

        if not progressed:
            break

        # Intentar pasar a la siguiente pagina de mesas (numero actual + 1)
        if len(already_done) + len(newly_downloaded) >= max_mesas:
            break
        target_page_label = f"{page_num + 1:02d}"
        next_page_el = None
        for page_div in page.query_selector_all("app-custom-paginator .page"):
            if page_div.inner_text().strip() == target_page_label:
                next_page_el = page_div
                break
        if next_page_el:
            next_page_el.click()
            page.wait_for_timeout(500)
            page_num += 1
        else:
            break

    return newly_downloaded


def load_checkpoint(departamento_code: str, suffix: str = ""):
    path = os.path.join(DATA_DIR, f"checkpoint_{departamento_code}{suffix}.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return path, set(json.load(f))
    return path, set()


def flatten_puestos(node):
    """Convierte el arbol Municipio->Zona->Puesto en una lista plana
    [(municipio_code, municipio_name, zona_code, puesto_code, stand_name, count_table), ...].
    count_table es el total de mesas que ese puesto DEBERIA tener segun el
    arbol oficial -- se usa para saber si ya esta completo o aun faltan mesas
    por publicarse."""
    flat = []
    for m in node["municipalities"]:
        for z in m["zones"]:
            for s in z["stands"]:
                flat.append((
                    m["municipalityCode"], m.get("municipalityName", ""),
                    z["idZoneCode"], s["standCode"], s["standName"], s["countTable"],
                ))
    return flat


def append_checkpoint(checkpoint_path: str, done: set, key: str):
    done.add(key)
    with open(checkpoint_path, "w", encoding="utf-8") as f:
        json.dump(sorted(done), f)


def run(departamento_code: str, max_puestos: int, max_mesas: int, upload: bool, log=print, target=None,
        resume=True, puestos_subset=None, checkpoint_suffix="", headless=False):
    """target: tupla opcional (municipio_code, zona_code, puesto_code) para apuntar
    a un puesto especifico en lugar de tomar los primeros que aparezcan en el arbol.
    resume: si True, se salta mesas ya descargadas segun el checkpoint local (esto
    es por MESA, no por puesto -- si un puesto solo tenia algunas mesas publicadas
    en una corrida anterior, la siguiente corrida completa las que falten sin
    repetir las que ya estan).
    puestos_subset: lista opcional de tuplas (municipio, zona, puesto, nombre, count_table)
    a procesar en lugar de todo el departamento -- usado por el runner paralelo
    para repartir trabajo.
    checkpoint_suffix: sufijo del archivo de checkpoint, para que cada worker en paralelo
    tenga su propio archivo y no haya colisiones de escritura."""
    node = load_tree(departamento_code)
    log(f"Departamento: {node['departmentName']} ({departamento_code})")

    puestos = puestos_subset if puestos_subset is not None else flatten_puestos(node)
    total_puestos = len(puestos)

    checkpoint_path, done_mesas = load_checkpoint(departamento_code, checkpoint_suffix)
    if resume and done_mesas:
        log(f"Reanudando: {len(done_mesas)} mesas ya descargadas se omitiran.")

    puestos_procesados = 0
    puestos_saltados_completos = 0
    errores = 0
    resultados = []

    portal_origin = urlsplit(PORTAL_URL)
    department_url = f"{portal_origin.scheme}://{portal_origin.netloc}/departamento/{departamento_code}"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        # Se navega directo a /departamento/{codigo} en lugar de hacer clic en
        # el enlace de la portada: ese enlace puede no estar visible si el
        # departamento queda en otra pagina de la paginacion de la portada
        # (ej. MAGDALENA no aparece en la primera pagina, alfabeticamente).
        # Reintenta unas veces: un cold-start de Chromium/Akamai a veces tarda
        # mas de lo esperado, sobre todo en modo continuo con varios lanzamientos
        # seguidos del navegador.
        for intento in range(3):
            try:
                page.goto(department_url, wait_until="networkidle")
                page.wait_for_selector("app-consult app-custom-select", timeout=20000)
                break
            except Exception as e:
                if intento == 2:
                    raise
                log(f"AVISO: fallo la navegacion inicial (intento {intento + 1}/3): {e!r}. Reintentando...")

        for municipio_code, municipio_name, zona_code, puesto_code, stand_name, count_table in puestos:
            if puestos_procesados >= max_puestos:
                break
            puesto_key = f"{municipio_code}|{zona_code}|{puesto_code}"

            if target and (municipio_code, zona_code, puesto_code) != target:
                continue

            expected_total = min(max_mesas, count_table)
            done_count = sum(1 for k in done_mesas if k.startswith(f"{puesto_key}|"))
            if resume and done_count >= expected_total and expected_total > 0:
                puestos_saltados_completos += 1
                continue

            try:
                selects = page.query_selector_all("app-consult app-custom-select")
                _, municipio_select, zona_select, puesto_select = selects[0], selects[1], selects[2], selects[3]

                ok_m = click_item_by_code(page, municipio_select, municipio_code)
                if not ok_m:
                    log(f"AVISO: no encontre municipio {municipio_code}, salto.")
                    continue
                ok_z = click_item_by_code(page, zona_select, zona_code, mode="zona")
                if not ok_z:
                    log(f"AVISO: no encontre zona {zona_code} en municipio {municipio_code}, salto.")
                    continue
                ok_p = click_item_by_code(page, puesto_select, puesto_code)
                if not ok_p:
                    log(f"AVISO: no encontre puesto {puesto_code}, salto.")
                    continue

                page.click("app-consult .consult-btn button")
                page.wait_for_timeout(800)

                log(f"[{puestos_procesados + 1}/{total_puestos}] Puesto {puesto_key} - {stand_name} "
                    f"({done_count}/{count_table} ya descargadas)")
                nuevas = download_mesas_for_puesto(
                    page, departamento_code, node["departmentName"], municipio_code, municipio_name,
                    zona_code, puesto_code, stand_name, max_mesas, log,
                    checkpoint_path, done_mesas, upload,
                )
                resultados.extend(nuevas)

            except Exception as e:
                errores += 1
                log(f"ERROR en puesto {puesto_key}: {e!r} -- continuo con el siguiente.")
                try:
                    page.goto(department_url, wait_until="networkidle")
                    page.wait_for_selector("app-consult app-custom-select", timeout=15000)
                except Exception:
                    log("No pude recuperar la pagina tras el error en este puesto.")

            puestos_procesados += 1

        browser.close()

    log(f"\nTotal mesas nuevas descargadas: {len(resultados)} | "
        f"Puestos ya completos (saltados): {puestos_saltados_completos} | "
        f"Puestos con error: {errores}")
    return resultados


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--departamento", required=True, help="Codigo de departamento, ej: 01")
    parser.add_argument("--max-puestos", type=int, default=1)
    parser.add_argument("--max-mesas", type=int, default=2)
    parser.add_argument("--upload", action="store_true", help="Subir a Azure Blob Storage")
    parser.add_argument("--municipio", default=None, help="Codigo de municipio para apuntar a un puesto especifico")
    parser.add_argument("--zona", default=None, help="Codigo de zona para apuntar a un puesto especifico")
    parser.add_argument("--puesto", default=None, help="Codigo de puesto para apuntar a un puesto especifico")
    parser.add_argument("--no-resume", action="store_true", help="Ignorar el checkpoint y reprocesar todo desde cero")
    args = parser.parse_args()

    target = None
    if args.municipio and args.zona and args.puesto:
        target = (args.municipio, args.zona, args.puesto)

    run(args.departamento, args.max_puestos, args.max_mesas, args.upload, target=target, resume=not args.no_resume)
