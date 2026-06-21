"""
Runner iterativo 24h para el scraper E-14.

Ejecuta scraper_parallel.py cada INTERVAL_MINUTES durante DURATION_HOURS.
El Excel de registro y los checkpoints garantizan que no se re-procesen
actas ya enviadas entre iteraciones.

Logica de contenedor (hora Colombia / America/Bogota):
    00:00 - 22:59  ->  actas-e14-scraping-1
    23:00 - 23:59  ->  actas-e14-scraping-2

Uso:
    python src/loop_runner.py --departamento 01 --workers 8 --machine-id vm1
    python src/loop_runner.py --departamento 01 --workers 8 --machine-id vm1 \\
        --municipios 001,007,010,...
"""
import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9
    from backports.zoneinfo import ZoneInfo

TZ_BOGOTA = ZoneInfo("America/Bogota")
CONTAINER_DAY = "actas-e14-scraping-1"    # 00:00 - 22:59 hora Colombia
CONTAINER_NIGHT = "actas-e14-scraping-2"  # 23:00 - 23:59 hora Colombia

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_SCRIPT_DIR, "..", "data")
DEFAULT_EXCEL = os.path.join(DATA_DIR, "registro_actas.xlsx")
PARALLEL_SCRIPT = os.path.join(_SCRIPT_DIR, "scraper_parallel.py")

_stop_event = threading.Event()


def _handle_signal(signum, frame):
    print(f"\n[loop] Senal {signum} recibida — deteniendo al terminar la iteracion actual...")
    _stop_event.set()


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


def _ts() -> str:
    return datetime.now(TZ_BOGOTA).strftime("%Y-%m-%d %H:%M:%S")


def _active_container() -> str:
    """Devuelve el contenedor activo segun la hora Colombia."""
    hour = datetime.now(TZ_BOGOTA).hour
    return CONTAINER_NIGHT if hour >= 23 else CONTAINER_DAY


def _run_iteration(args, iteration: int) -> int:
    """Lanza una iteracion de scraper_parallel.py y devuelve el exit code."""
    container = _active_container()
    print(f"[{_ts()}] === Iteracion {iteration} | contenedor={container} ===", flush=True)

    cmd = [
        sys.executable, PARALLEL_SCRIPT,
        "--departamento", args.departamento,
        "--workers", str(args.workers),
        "--max-mesas", str(args.max_mesas),
        "--upload",
        "--container", container,
        "--excel", args.excel,
    ]
    if args.municipios:
        cmd += ["--municipios", args.municipios]
    if args.machine_id:
        cmd += ["--machine-id", args.machine_id]

    t0 = time.monotonic()
    result = subprocess.run(cmd)
    elapsed = time.monotonic() - t0

    status = "OK" if result.returncode == 0 else f"ERROR (exit={result.returncode})"
    print(f"[{_ts()}] Iteracion {iteration} finalizada en {elapsed:.0f}s — {status}", flush=True)
    return result.returncode


def main():
    parser = argparse.ArgumentParser(
        description="Runner iterativo 24h para el scraper E-14"
    )
    parser.add_argument("--departamento", required=True,
                        help="Codigo de departamento, ej: 01")
    parser.add_argument("--workers", type=int, default=8,
                        help="Numero de workers paralelos (default: 8)")
    parser.add_argument("--max-mesas", type=int, default=99999,
                        help="Maximo de mesas por puesto (default: sin limite)")
    parser.add_argument("--municipios", default=None,
                        help="Codigos de municipio separados por coma (ej: 001,004)")
    parser.add_argument("--machine-id", default="",
                        help="Identificador de esta maquina (ej: vm1)")
    parser.add_argument("--duration-hours", type=float, default=24.0,
                        help="Horas de vigencia del loop (default: 24)")
    parser.add_argument("--interval-minutes", type=float, default=10.0,
                        help="Minutos de espera entre iteraciones (default: 10)")
    parser.add_argument("--excel", default=DEFAULT_EXCEL,
                        help=f"Ruta del Excel de registro maestro (default: {DEFAULT_EXCEL})")
    args = parser.parse_args()

    deadline = datetime.now() + timedelta(hours=args.duration_hours)
    interval_sec = args.interval_minutes * 60
    iteration = 0

    print(f"[{_ts()}] loop: inicio")
    print(f"[{_ts()}] departamento={args.departamento} | workers={args.workers} | "
          f"machine-id={args.machine_id or '(sin id)'}")
    print(f"[{_ts()}] duracion={args.duration_hours}h | intervalo={args.interval_minutes}min")
    print(f"[{_ts()}] deadline={deadline.strftime('%Y-%m-%d %H:%M')} | excel={args.excel}")
    print(f"[{_ts()}] contenedor dia  (00-22h): {CONTAINER_DAY}")
    print(f"[{_ts()}] contenedor noche (23h)  : {CONTAINER_NIGHT}")
    print()

    while not _stop_event.is_set() and datetime.now() < deadline:
        iteration += 1
        _run_iteration(args, iteration)

        if _stop_event.is_set():
            break

        remaining = (deadline - datetime.now()).total_seconds()
        if remaining <= 0:
            break

        wait = min(interval_sec, remaining)
        print(f"[{_ts()}] proxima iteracion en {wait / 60:.1f} min "
              f"(restante: {remaining / 3600:.2f}h)...", flush=True)
        _stop_event.wait(timeout=wait)  # interrumpible por SIGTERM/SIGINT

    print(f"\n[{_ts()}] loop: fin — {iteration} iteraciones completadas.")


if __name__ == "__main__":
    main()
