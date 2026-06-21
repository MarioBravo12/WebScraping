"""
Calcula como repartir los municipios de un departamento entre N maquinas
virtuales de forma balanceada por cantidad de mesas (no por cantidad de
municipios -- un municipio puede tener 2 mesas y otro 2000).

Usa un algoritmo greedy "longest processing time": ordena los municipios de
mayor a menor cantidad de mesas, y en cada paso asigna el municipio a la
maquina que actualmente tiene menos carga. Imprime el comando exacto a
correr en cada maquina.

Uso:
    python src/plan_split.py --departamento 01 --maquinas 3 --workers-por-maquina 8
"""
import argparse

from scraper import load_tree


def municipio_mesa_counts(node):
    counts = {}
    names = {}
    for m in node["municipalities"]:
        code = m["municipalityCode"]
        total = sum(s["countTable"] for z in m["zones"] for s in z["stands"])
        counts[code] = counts.get(code, 0) + total
        names[code] = m.get("municipalityName", "")
    return counts, names


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--departamento", required=True)
    parser.add_argument("--maquinas", type=int, required=True)
    parser.add_argument("--workers-por-maquina", type=int, default=8)
    parser.add_argument("--upload", action="store_true")
    args = parser.parse_args()

    node = load_tree(args.departamento)
    counts, names = municipio_mesa_counts(node)

    items = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    machines = [{"municipios": [], "total": 0} for _ in range(args.maquinas)]

    for code, total in items:
        target = min(machines, key=lambda m: m["total"])
        target["municipios"].append(code)
        target["total"] += total

    print(f"Departamento: {node['departmentName']} ({args.departamento})")
    print(f"Total mesas: {sum(counts.values())}, {len(counts)} municipios, repartidos en {args.maquinas} maquinas\n")

    upload_flag = " --upload" if args.upload else ""
    for i, m in enumerate(machines, start=1):
        codigos = ",".join(sorted(m["municipios"]))
        print(f"--- VM {i}: {len(m['municipios'])} municipios, {m['total']} mesas ---")
        print(
            f"python src/scraper_parallel.py --departamento {args.departamento} "
            f"--workers {args.workers_por_maquina}{upload_flag} "
            f"--municipios {codigos} --machine-id vm{i}\n"
        )


if __name__ == "__main__":
    main()
