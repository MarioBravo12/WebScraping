"""
Genera un Markdown con el listado de municipios (codigo, nombre, mesas) de
los departamentos indicados, para referencia rapida al armar grupos de
despliegue con --municipios. Por defecto genera el listado de los 3
departamentos del piloto (Antioquia, Atlantico, Magdalena).

Ejecutar:
    python src/list_municipios.py
    python src/list_municipios.py --departamentos 01,03,21,05
"""
import argparse
import os

from scraper import load_tree

OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "municipios_por_departamento.md")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--departamentos", default="01,03,21",
                         help="Codigos de departamento separados por coma (default: 01,03,21)")
    args = parser.parse_args()
    codigos = [c.strip() for c in args.departamentos.split(",") if c.strip()]

    lines = ["# Municipios por departamento\n",
             "Generado con `src/list_municipios.py` a partir de `reference_data/departmentsTree.json`.\n"]

    for depto_code in codigos:
        node = load_tree(depto_code)
        municipios = {}
        for m in node["municipalities"]:
            code = m["municipalityCode"]
            total = sum(s["countTable"] for z in m["zones"] for s in z["stands"])
            municipios[code] = (m.get("municipalityName", ""), municipios.get(code, (None, 0))[1] + total)

        total_mesas = sum(t for _, t in municipios.values())
        lines.append(f"\n## {node['departmentName']} ({depto_code}) — {len(municipios)} municipios, {total_mesas} mesas\n")
        lines.append("| Código | Municipio | Mesas |")
        lines.append("|---|---|---|")
        for code, (name, total) in sorted(municipios.items()):
            lines.append(f"| {code} | {name} | {total} |")

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Guardado: {OUT_PATH}")


if __name__ == "__main__":
    main()
