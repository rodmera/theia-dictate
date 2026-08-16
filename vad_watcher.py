#!/usr/bin/env python3
"""VAD watcher — espera a que vad.py corte por silencio y dispara la transcripción.

Similar a silence_watcher.py pero para el modo auto_stop='vad': vad.py escribe
`/tmp/theia-dictate-vad-status.json` cuando detecta fin de voz; este watcher lo
espera y llama al script principal para transcribir.
"""
import os
import subprocess
import sys
import time

PID_FILE = "/tmp/theia-dictate.pid"
STATUS_FILE = "/tmp/theia-dictate-vad-status.json"
MAX_WAIT_S = 300


def main():
    if len(sys.argv) < 2:
        return
    script_path = sys.argv[1]

    # Resetear status previo
    try:
        os.remove(STATUS_FILE)
    except FileNotFoundError:
        pass

    deadline = time.time() + MAX_WAIT_S
    while time.time() < deadline:
        if os.path.exists(STATUS_FILE):
            break
        # Si el usuario apagó manualmente el PID (fin normal), salir
        if not os.path.exists(PID_FILE):
            return
        time.sleep(0.1)

    # Disparar stop solo si seguimos en modo grabación.
    # Siempre `record stop` (nunca toggle): tras el corte de voz un toggle
    # reiniciaría la grabación por carrera con stop_and_transcribe.
    if os.path.exists(PID_FILE):
        subprocess.run([sys.executable, script_path, "record", "stop"])


if __name__ == "__main__":
    main()
