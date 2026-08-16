#!/usr/bin/env python3
"""Espera a que sox termine (silencio detectado) y dispara la transcripción automáticamente."""
import os
import sys
import subprocess
import time

PID_FILE = "/tmp/theia-dictate.pid"


def main():
    if len(sys.argv) < 3:
        return
    sox_pid = int(sys.argv[1])
    script_path = sys.argv[2]

    # Esperar que sox termine (silencio detectado o SIGINT manual)
    while True:
        try:
            os.kill(sox_pid, 0)
            time.sleep(0.1)
        except ProcessLookupError:
            break

    # Disparar stop solo si aún estamos en modo grabación
    if os.path.exists(PID_FILE):
        # Siempre stop directo (nunca toggle): al terminar el silencio el
        # recorder ya murió y un toggle reiniciaría la grabación por carrera
        # con el stop de stop_and_transcribe (incidente 2026-08-15).
        subprocess.run([sys.executable, script_path, "record", "stop"])


if __name__ == '__main__':
    main()
