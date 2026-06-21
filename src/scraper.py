"""
Scraper de formularios E-14 - Registraduria Nacional (portal Visor Ciudadano).

Recorre Departamento -> Municipio -> Zona -> Puesto -> Mesa automatizando la
navegacion real del sitio (los selects son componentes Angular personalizados,
no <select> nativos), intercepta la respuesta HTTP que entrega el PDF real
(antes de que el navegador lo convierta en blob) y sube cada archivo a Azure
Blob Storage.

Uso:
    python src/scraper.py --departamento 01 --container actas-e14-scraping-1
    python src/scraper.py --departamento 01 --max-puestos 1 --max-mesas 2

Por seguridad, por defecto SOLO procesa 1 puesto y 2 mesas (modo de prueba).
Aumenta --max-puestos/--max-mesas explicitamente para correr a mayor escala.
"""
import argparse
import hashlib
import io
import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

import unicodedata

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

import excel_registry
from azure_uploader import upload_file

load_dotenv()


def _safe_folder(name: str, max_len: int = 50) -> str:
    """Normaliza un nombre para usarlo como carpeta en Azure Blob Storage.

    Elimina tildes/diacriticos, reemplaza espacios y caracteres especiales
    por guion bajo, convierte a minusculas y trunca.
    Ej: 'INST. EDU. BELLAVISTA Nº2' -> 'inst_edu_bellavista_n_2'
    """
    nfkd = unicodedata.normalize("NFKD", name or "")
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    safe = re.sub(r"[^A-Za-z0-9\-]", "_", ascii_str)
    safe = re.sub(r"_+", "_", safe).strip("_").lower()
    return safe[:max_len] or "sin_nombre"

# Si la Registraduria cambia el dominio/ruta del portal (ej. para un dia de
# elecciones distinto o una segunda vuelta), defina PORTAL_URL en .env -- NO
# hace falta tocar el codigo.
PORTAL_URL = os.environ.get("PORTAL_URL") or "https://e14segundavueltapresidente.registraduria.gov.co/home"
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
REFERENCE_DIR = os.path.join(os.path.dirname(__file__), "..", "reference_data")
TREE_PATH = os.path.join(REFERENCE_DIR, "departmentsTree.json")
DOWNLOADS_DIR = os.path.join(DATA_DIR, "downloads")
DEFAULT_EXCEL_PATH = os.path.join(DATA_DIR, "registro_actas.xlsx")


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


def _pdf_page_count(pdf_bytes: bytes):
    """Devuelve el numero de paginas del PDF, o None si pypdf no esta disponible."""
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        return len(reader.pages)
    except Exception:
        return None


def download_mesas_for_puesto(page, depto_code, depto_name, municipio_code, municipio_name,
                               zona_code, puesto_code, puesto_name, max_mesas, log,
                               registry_keys=None):
    """Asume que ya se navego al departamento y se hizo clic en Consultar.
    Itera las tarjetas 'Mesa N' visibles (con paginacion) y descarga cada PDF,
    junto con un .json de metadata enriquecida.

    registry_keys: si se pasa, omite mesas ya registradas como enviadas."""
    downloaded = []
    seen_mesas = set()

    page_num = 1
    while len(downloaded) < max_mesas:
        cards = page.query_selector_all("app-consult .item-table.isAvailable")
        if not cards:
            break

        progressed = False
        for card in cards:
            if len(downloaded) >= max_mesas:
                break
            title_el = card.query_selector("h3")
            mesa_label = title_el.inner_text().strip() if title_el else ""
            if mesa_label in seen_mesas:
                continue
            seen_mesas.add(mesa_label)
            progressed = True

            mesa_num = re.sub(r"\D", "", mesa_label).zfill(3) or "000"

            if registry_keys is not None and excel_registry.is_uploaded(
                registry_keys, depto_code, municipio_code, zona_code, puesto_code, mesa_num
            ):
                log(f"  (ya enviada, omitiendo {mesa_label})")
                continue

            download_icon = card.query_selector(".open-pdf")
            try:
                with page.expect_response(
                    lambda r: "/assets/temis/pdf/" in r.url and r.status == 200, timeout=8000
                ) as resp_info:
                    download_icon.click()
                response = resp_info.value
                pdf_bytes = response.body()
                download_url = response.url

                # La URL real tiene el patron:
                # .../assets/temis/pdf/{depto}/{municipio}/{zona}/{puesto}/{mesa}/{corp}/{hash}.pdf
                url_path = urlsplit(download_url).path
                path_parts = url_path.rstrip("/").split("/")
                corp_code = path_parts[-2] if len(path_parts) >= 2 else None
                hash_filename = path_parts[-1] if path_parts else None

                sha256 = hashlib.sha256(pdf_bytes).hexdigest()
                num_pages = _pdf_page_count(pdf_bytes)

                fname = f"{depto_code}_{municipio_code}_{zona_code}_{puesto_code}_{mesa_num}.pdf"
                os.makedirs(DOWNLOADS_DIR, exist_ok=True)
                local_path = os.path.join(DOWNLOADS_DIR, fname)
                with open(local_path, "wb") as f:
                    f.write(pdf_bytes)

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
                    "url_descarga": download_url,
                    "hash_archivo_original": hash_filename,
                    "sha256": sha256,
                    "tamano_bytes": len(pdf_bytes),
                    "numero_paginas": num_pages,
                    "mime_tipo": "application/pdf",
                    "fecha_descarga_utc": datetime.now(timezone.utc).isoformat(),
                    # Los campos blob_* se rellenan en run() tras el upload
                    "container": None,
                    "blob_nombre": None,
                    "blob_url": None,
                    "blob_metadata_url": None,
                    "fecha_subida_utc": None,
                }
                metadata_path = os.path.splitext(local_path)[0] + ".json"
                with open(metadata_path, "w", encoding="utf-8") as f:
                    json.dump(metadata, f, ensure_ascii=False, indent=2)

                log(f"  Descargado {mesa_label} -> {fname} ({len(pdf_bytes)} bytes, sha256={sha256[:12]}…)")
                downloaded.append({
                    "mesa": mesa_label,
                    "local_path": local_path,
                    "metadata_path": metadata_path,
                    "mesa_num": mesa_num,
                })
            except Exception as e:
                log(f"  AVISO: no se pudo interceptar el PDF de {mesa_label}: {e!r}")

            dismiss_blocking_modal(page, log, wait_ms=150)

        if not progressed:
            break

        if len(downloaded) >= max_mesas:
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

    return downloaded


def load_checkpoint(departamento_code: str, suffix: str = ""):
    path = os.path.join(DATA_DIR, f"checkpoint_{departamento_code}{suffix}.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return path, set(json.load(f))
    return path, set()


def flatten_puestos(node):
    """Convierte el arbol Municipio->Zona->Puesto en una lista plana
    [(municipio_code, municipio_name, zona_code, puesto_code, stand_name), ...]."""
    flat = []
    for m in node["municipalities"]:
        for z in m["zones"]:
            for s in z["stands"]:
                flat.append((
                    m["municipalityCode"], m.get("municipalityName", ""),
                    z["idZoneCode"], s["standCode"], s["standName"],
                ))
    return flat


def append_checkpoint(checkpoint_path: str, done: set, key: str):
    done.add(key)
    with open(checkpoint_path, "w", encoding="utf-8") as f:
        json.dump(sorted(done), f)


def run(departamento_code: str, max_puestos: int, max_mesas: int, upload: bool,
        log=print, target=None, resume=True, puestos_subset=None, checkpoint_suffix="",
        headless=False, container_name: str = None, excel_path: str = None,
        initial_registry_keys=None):
    """
    container_name: contenedor Azure destino (requerido si upload=True).
    excel_path: ruta del Excel de registro de este worker (None = no registrar).
    initial_registry_keys: set de claves ya subidas (del registro maestro) para
      que este worker las omita sin necesidad de leer el Excel maestro en cada mesa.
    target: tupla opcional (municipio_code, zona_code, puesto_code).
    puestos_subset: lista opcional para el runner paralelo.
    """
    if upload and not container_name:
        raise ValueError("container_name es requerido cuando upload=True")

    node = load_tree(departamento_code)
    log(f"Departamento: {node['departmentName']} ({departamento_code})")
    if container_name:
        log(f"Contenedor destino: {container_name}")

    puestos = puestos_subset if puestos_subset is not None else flatten_puestos(node)
    total_puestos = len(puestos)

    checkpoint_path, done_puestos = load_checkpoint(departamento_code, checkpoint_suffix)
    if resume and done_puestos:
        log(f"Reanudando: {len(done_puestos)} puestos ya completados se omitiran.")

    registry_keys, registry_wb = excel_registry.load_registry(excel_path) if excel_path else (set(), None)
    # Precarga las claves del registro maestro (ejecuciones anteriores / otros workers)
    if initial_registry_keys:
        registry_keys |= initial_registry_keys
    if excel_path:
        log(f"Registro Excel: {excel_path} ({len(registry_keys)} actas conocidas)")

    puestos_procesados = 0
    errores = 0
    resultados = []

    portal_origin = urlsplit(PORTAL_URL)
    department_url = f"{portal_origin.scheme}://{portal_origin.netloc}/departamento/{departamento_code}"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        page.goto(department_url, wait_until="networkidle")
        page.wait_for_selector("app-consult app-custom-select", timeout=15000)

        for municipio_code, municipio_name, zona_code, puesto_code, stand_name in puestos:
            if puestos_procesados >= max_puestos:
                break
            puesto_key = f"{municipio_code}|{zona_code}|{puesto_code}"

            if target and (municipio_code, zona_code, puesto_code) != target:
                continue
            if resume and puesto_key in done_puestos:
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

                log(f"[{puestos_procesados + 1}/{total_puestos}] Puesto {puesto_key} - {stand_name}")
                descargados = download_mesas_for_puesto(
                    page, departamento_code, node["departmentName"], municipio_code, municipio_name,
                    zona_code, puesto_code, stand_name, max_mesas, log,
                    registry_keys=registry_keys,
                )

                for item in descargados:
                    blob_url = None
                    metadata_blob_url = None
                    blob_name = None
                    if upload:
                        # Estructura: {depto}/{municipio}/{lugar}/{zona}/{puesto}/{mesa}/
                        # "lugar" = nombre del puesto de votacion (normalizado para blob)
                        lugar = _safe_folder(stand_name)
                        mesa_folder = item["mesa_num"]
                        prefix = (
                            f"{departamento_code}/{municipio_code}"
                            f"/{lugar}/{zona_code}/{puesto_code}/{mesa_folder}"
                        )
                        blob_name = f"{prefix}/acta.pdf"
                        meta_blob_name = f"{prefix}/metadata.json"
                        blob_url = upload_file(item["local_path"], blob_name, container_name)
                        metadata_blob_url = upload_file(item["metadata_path"], meta_blob_name, container_name)

                        # Actualizar el JSON local con la info de Azure
                        with open(item["metadata_path"], "r", encoding="utf-8") as f:
                            meta = json.load(f)
                        meta["container"] = container_name
                        meta["blob_nombre"] = blob_name
                        meta["blob_url"] = blob_url
                        meta["blob_metadata_url"] = metadata_blob_url
                        meta["fecha_subida_utc"] = datetime.now(timezone.utc).isoformat()
                        with open(item["metadata_path"], "w", encoding="utf-8") as f:
                            json.dump(meta, f, ensure_ascii=False, indent=2)

                        if excel_path:
                            excel_registry.append_record(excel_path, registry_keys, registry_wb, meta)

                    resultados.append({
                        "mesa": item["mesa"],
                        "local_path": item["local_path"],
                        "metadata_path": item["metadata_path"],
                        "blob_url": blob_url,
                        "metadata_blob_url": metadata_blob_url,
                    })

                append_checkpoint(checkpoint_path, done_puestos, puesto_key)

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

    log(f"\nTotal mesas descargadas: {len(resultados)} | Puestos con error (saltados): {errores}")
    return resultados


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--departamento", required=True, help="Codigo de departamento, ej: 01")
    parser.add_argument("--container", default=None,
                        help="Contenedor Azure destino (requerido con --upload). "
                             "Ej: actas-e14-scraping-1 | actas-e14-scraping-2")
    parser.add_argument("--max-puestos", type=int, default=1)
    parser.add_argument("--max-mesas", type=int, default=2)
    parser.add_argument("--upload", action="store_true", help="Subir a Azure Blob Storage")
    parser.add_argument("--excel", default=DEFAULT_EXCEL_PATH,
                        help=f"Ruta del Excel de registro (default: {DEFAULT_EXCEL_PATH})")
    parser.add_argument("--municipio", default=None)
    parser.add_argument("--zona", default=None)
    parser.add_argument("--puesto", default=None)
    parser.add_argument("--no-resume", action="store_true",
                        help="Ignorar el checkpoint y reprocesar todo desde cero")
    parser.add_argument("--headless", action="store_true", help="Ejecutar el navegador en modo headless")
    args = parser.parse_args()

    if args.upload and not args.container:
        parser.error("--container es requerido cuando se usa --upload. "
                     "Usa: --container actas-e14-scraping-1 o --container actas-e14-scraping-2")

    target = None
    if args.municipio and args.zona and args.puesto:
        target = (args.municipio, args.zona, args.puesto)

    run(
        args.departamento,
        args.max_puestos,
        args.max_mesas,
        args.upload,
        target=target,
        resume=not args.no_resume,
        container_name=args.container,
        excel_path=args.excel,
        headless=args.headless,
    )
