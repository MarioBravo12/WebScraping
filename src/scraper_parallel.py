"""
Lanza N workers en paralelo (procesos independientes, cada uno con su propio
navegador Playwright) repartiendo la lista de puestos de un departamento entre
ellos. Cada worker tiene su propio checkpoint (checkpoint_{depto}_{machine-id}_w{N}.json)
para poder reanudar sin colisiones. El checkpoint es por MESA individual, asi
que correr esto varias veces solo descarga lo que falte (ver README).

Uso en una sola maquina (una sola pasada -- descarga lo que este disponible
HOY y termina):
    python src/scraper_parallel.py --departamento 01 --workers 8 --upload

Modo continuo (recomendado durante una eleccion en curso, mientras se siguen
publicando resultados): se queda corriendo, reintenta cada N minutos, y
termina solo cuando se complete el 100% de las mesas esperadas (o se agote
--max-poll-horas):
    python src/scraper_parallel.py --departamento 01 --workers 8 --upload \
        --poll-minutos 10 --max-poll-horas 12

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
import time

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


def run_once(departamento_code, shards, max_mesas, upload, max_puestos_por_worker, checkpoint_prefix):
    processes = []
    for i, shard in enumerate(shards):
        p = mp.Process(
            target=worker_main,
            args=(i, departamento_code, shard, max_mesas, upload, max_puestos_por_worker, checkpoint_prefix),
        )
        p.start()
        processes.append(p)
    for p in processes:
        p.join()


def count_progress(departamento_code, all_puestos, num_workers, checkpoint_prefix):
    """Suma cuantas mesas ya estan en los checkpoints de todos los workers de
    esta corrida, contra el total esperado segun el arbol oficial."""
    expected_total = sum(p[5] for p in all_puestos)
    valid_prefixes = {f"{p[0]}|{p[2]}|{p[3]}|" for p in all_puestos}

    done_keys = set()
    for i in range(num_workers):
        path = os.path.join(DATA_DIR, f"checkpoint_{departamento_code}_{checkpoint_prefix}w{i}.json")
        if os.path.exists(path):
            import json
            with open(path, "r", encoding="utf-8") as f:
                for key in json.load(f):
                    if any(key.startswith(prefix) for prefix in valid_prefixes):
                        done_keys.add(key)

    return len(done_keys), expected_total


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
    parser.add_argument("--poll-minutos", type=float, default=None,
                         help="Si se especifica, no termina tras una pasada: reintenta cada N "
                              "minutos hasta completar el 100%% de las mesas esperadas o agotar "
                              "--max-poll-horas. Sin este flag, corre una sola pasada y termina "
                              "(comportamiento anterior).")
    parser.add_argument("--max-poll-horas", type=float, default=12,
                         help="Tope de tiempo total en modo continuo, para no quedar corriendo "
                              "para siempre si algunas mesas nunca se publican (ej. mesas anuladas). "
                              "Default 12 horas.")
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

    start_time = time.time()
    intento = 0
    while True:
        intento += 1
        print(f"\n=== Pasada {intento} ===")
        run_once(args.departamento, shards, args.max_mesas, args.upload, args.max_puestos_por_worker, checkpoint_prefix)

        if args.poll_minutos is None:
            print("Todos los workers terminaron (una sola pasada).")
            break

        done, expected = count_progress(args.departamento, all_puestos, args.workers, checkpoint_prefix)
        pct = (done / expected * 100) if expected else 100
        print(f"Progreso: {done}/{expected} mesas ({pct:.1f}%)")

        if done >= expected:
            print("Completo: se alcanzo el 100% de las mesas esperadas.")
            break

        elapsed_hours = (time.time() - start_time) / 3600
        if elapsed_hours >= args.max_poll_horas:
            print(f"Se agoto --max-poll-horas ({args.max_poll_horas}h) sin completar el 100%. "
                  f"Quedaron {expected - done} mesas pendientes (probablemente no publicadas aun). "
                  f"Vuelve a correr el mismo comando mas tarde para continuar.")
            break

        print(f"Esperando {args.poll_minutos} min antes de reintentar...")
        time.sleep(args.poll_minutos * 60)


if __name__ == "__main__":
    main()
