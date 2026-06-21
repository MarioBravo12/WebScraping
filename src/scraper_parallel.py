"""
Lanza N workers en paralelo (procesos independientes, cada uno con su propio
navegador Playwright) repartiendo la lista de puestos de un departamento entre
ellos. Cada worker tiene su propio checkpoint y su propio Excel parcial;
al terminar todos los workers se fusionan en el registro maestro.

Uso en una sola maquina:
    python src/scraper_parallel.py --departamento 01 --workers 8 --upload \\
        --container actas-e14-scraping-1

Para repartir el trabajo entre varias maquinas virtuales:

    # VM 1
    python src/scraper_parallel.py --departamento 01 --workers 8 --upload \\
        --container actas-e14-scraping-1 \\
        --municipios 001,004,007,010 --machine-id vm1
    # VM 2
    python src/scraper_parallel.py --departamento 01 --workers 8 --upload \\
        --container actas-e14-scraping-1 \\
        --municipios 013,016,020 --machine-id vm2
"""
import argparse
import multiprocessing as mp
import os
import sys

import excel_registry
from scraper import flatten_puestos, load_tree, run

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def _worker_excel_path(master_excel: str, checkpoint_prefix: str, worker_id: int) -> str:
    base, ext = os.path.splitext(master_excel)
    return f"{base}_{checkpoint_prefix}w{worker_id}{ext}"


def worker_main(worker_id, departamento_code, puestos_subset, max_mesas, upload,
                max_puestos, checkpoint_prefix, container_name, master_excel):
    log_path = os.path.join(DATA_DIR, f"worker_{departamento_code}_{checkpoint_prefix}{worker_id}.log")
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(log_path, "w", encoding="utf-8", buffering=1) as f:
        def log(line):
            print(f"[W{worker_id}] {line}", flush=True)
            f.write(line + "\n")

        # Lee el registro maestro (actas subidas en iteraciones anteriores)
        # para que este worker no las re-descargue. Lectura concurrente = segura.
        master_keys = set()
        if master_excel and os.path.exists(master_excel):
            master_keys, _ = excel_registry.load_registry(master_excel)
            log(f"Registro maestro: {len(master_keys)} actas ya subidas, se omitiran.")

        worker_excel = _worker_excel_path(master_excel, checkpoint_prefix, worker_id) if master_excel else None

        run(
            departamento_code,
            max_puestos=max_puestos,
            max_mesas=max_mesas,
            upload=upload,
            log=log,
            puestos_subset=puestos_subset,
            checkpoint_suffix=f"_{checkpoint_prefix}w{worker_id}",
            headless=False,
            container_name=container_name,
            excel_path=worker_excel,
            initial_registry_keys=master_keys,
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--departamento", required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-mesas", type=int, default=99999,
                        help="Maximo de mesas por puesto")
    parser.add_argument("--max-puestos-por-worker", type=int, default=99999)
    parser.add_argument("--upload", action="store_true")
    parser.add_argument("--container", default=None,
                        help="Contenedor Azure destino (requerido con --upload). "
                             "Ej: actas-e14-scraping-1 | actas-e14-scraping-2")
    parser.add_argument("--municipios", default=None,
                        help="Codigos de municipio separados por coma (ej: 001,004,007). "
                             "Si se omite, procesa todo el departamento.")
    parser.add_argument("--machine-id", default="",
                        help="Identificador de esta maquina (ej: vm1). Evita colisiones "
                             "de checkpoint entre VMs.")
    parser.add_argument("--excel", default=None,
                        help="Ruta del Excel de registro maestro. Cada worker escribe en "
                             "un archivo propio y al final se fusionan aqui. "
                             "Si se omite, no se usa registro Excel.")
    args = parser.parse_args()

    if args.upload and not args.container:
        parser.error("--container es requerido cuando se usa --upload. "
                     "Usa: --container actas-e14-scraping-1 o --container actas-e14-scraping-2")

    node = load_tree(args.departamento)
    all_puestos = flatten_puestos(node)

    if args.municipios:
        wanted = {c.strip() for c in args.municipios.split(",") if c.strip()}
        all_puestos = [p for p in all_puestos if p[0] in wanted]
        print(f"Filtrado a municipios {sorted(wanted)}: {len(all_puestos)} puestos")
    else:
        print(f"Total puestos en {node['departmentName']}: {len(all_puestos)}")

    if not all_puestos:
        print("No hay puestos que procesar con ese filtro. Revisa --municipios.")
        return

    checkpoint_prefix = f"{args.machine_id}_" if args.machine_id else ""

    # Reparte los puestos en N listas (round-robin) para balancear carga
    shards = [[] for _ in range(args.workers)]
    for i, puesto in enumerate(all_puestos):
        shards[i % args.workers].append(puesto)

    for i, shard in enumerate(shards):
        print(f"  Worker {i}: {len(shard)} puestos")

    processes = []
    for i, shard in enumerate(shards):
        p = mp.Process(
            target=worker_main,
            args=(i, args.departamento, shard, args.max_mesas, args.upload,
                  args.max_puestos_por_worker, checkpoint_prefix,
                  args.container, args.excel),
        )
        p.start()
        processes.append(p)

    for p in processes:
        p.join()

    # Fusionar registros por-worker en el maestro
    if args.excel:
        per_worker = [
            _worker_excel_path(args.excel, checkpoint_prefix, i)
            for i in range(args.workers)
        ]
        added = excel_registry.merge_registries(per_worker, args.excel)
        print(f"Registro Excel fusionado -> {args.excel} (+{added} nuevas actas)")

    print("Todos los workers terminaron.")


if __name__ == "__main__":
    main()
