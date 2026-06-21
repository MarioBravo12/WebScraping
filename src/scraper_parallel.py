"""
Lanza N workers en paralelo (procesos independientes, cada uno con su propio
navegador Playwright) repartiendo la lista de puestos de un departamento entre
ellos. Cada worker tiene su propio checkpoint (checkpoint_{depto}_{machine-id}w{N}.json)
para poder reanudar sin colisiones.

Uso en una sola maquina:
    python src/scraper_parallel.py --departamento 01 --workers 8 --upload

Para repartir el trabajo entre varias maquinas virtuales (mas rapido que
apilar workers en una sola, ver README): cada VM corre el mismo comando pero
con un subconjunto distinto de --municipios y un --machine-id distinto, por
ejemplo:

    # VM 1
    python src/scraper_parallel.py --departamento 01 --workers 8 --upload \
        --municipios 001,004,007,010 --machine-id vm1
    # VM 2
    python src/scraper_parallel.py --departamento 01 --workers 8 --upload \
        --municipios 013,016,020 --machine-id vm2

Usa src/plan_split.py para calcular automaticamente como repartir los
municipios entre N maquinas de forma balanceada por cantidad de mesas.

Para una prueba pequena (recomendado antes de escalar):
    python src/scraper_parallel.py --departamento 01 --workers 3 --max-puestos-por-worker 2
"""
import argparse
import multiprocessing as mp
import os

from scraper import flatten_puestos, load_tree, run

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def worker_main(worker_id, departamento_code, puestos_subset, max_mesas, upload, max_puestos, checkpoint_prefix):
    log_path = os.path.join(DATA_DIR, f"worker_{departamento_code}_{checkpoint_prefix}{worker_id}.log")
    with open(log_path, "w", encoding="utf-8", buffering=1) as f:
        def log(line):
            print(f"[W{worker_id}] {line}")
            f.write(line + "\n")

        run(
            departamento_code,
            max_puestos=max_puestos,
            max_mesas=max_mesas,
            upload=upload,
            log=log,
            puestos_subset=puestos_subset,
            checkpoint_suffix=f"_{checkpoint_prefix}w{worker_id}",
            headless=False,
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--departamento", required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-mesas", type=int, default=99999, help="Maximo de mesas por puesto")
    parser.add_argument("--max-puestos-por-worker", type=int, default=99999)
    parser.add_argument("--upload", action="store_true")
    parser.add_argument("--municipios", default=None,
                         help="Codigos de municipio separados por coma (ej: 001,004,007). "
                              "Si se omite, procesa todo el departamento. Usar para repartir "
                              "el trabajo entre varias maquinas virtuales.")
    parser.add_argument("--machine-id", default="",
                         help="Identificador de esta maquina (ej: vm1). Se usa como prefijo del "
                              "checkpoint para que varias VMs no colisionen si comparten almacenamiento.")
    args = parser.parse_args()

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
                  args.max_puestos_por_worker, checkpoint_prefix),
        )
        p.start()
        processes.append(p)

    for p in processes:
        p.join()

    print("Todos los workers terminaron.")


if __name__ == "__main__":
    main()
