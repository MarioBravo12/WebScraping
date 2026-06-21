"""
Descarga/actualiza los archivos JSON de referencia (arbol Departamento ->
Municipio -> Zona -> Puesto, y la lista de departamentos/corporaciones) que
scraper.py usa para saber que combinaciones existen. Se obtienen abriendo
el portal con un navegador real (la proteccion Akamai bloquea peticiones
HTTP simples sin sesion de navegador).

Ejecutar antes del primer uso, o si la Registraduria habilita una nueva
eleccion/corporacion:
    python src/fetch_tree_json.py
"""
import os
from playwright.sync_api import sync_playwright

PORTAL_URL = "https://e14segundavueltapresidente.registraduria.gov.co/home"
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "reference_data")

FILES = [
    "assets/temis/divipol_json/allDepartments.json",
    "assets/temis/divipol_json/departmentsTree.json",
    "assets/temis/divipol_json/allCorporations.json",
    "assets/temis/divipol_json/allMviewGetProgressByDepartmentAndCorporations.json",
]


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto(PORTAL_URL, wait_until="networkidle")

        for rel_path in FILES:
            url = f"https://e14segundavueltapresidente.registraduria.gov.co/{rel_path}"
            content = page.evaluate(
                """async (url) => {
                    const res = await fetch(url);
                    return await res.text();
                }""",
                url,
            )
            out_name = rel_path.split("/")[-1]
            if out_name == "allMviewGetProgressByDepartmentAndCorporations.json":
                out_name = "progress_by_department.json"
            out_path = os.path.join(OUT_DIR, out_name)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(content)
            size_kb = len(content) / 1024
            print(f"Guardado {out_name} ({size_kb:.1f} KB) -> {out_path}")

        browser.close()


if __name__ == "__main__":
    main()
